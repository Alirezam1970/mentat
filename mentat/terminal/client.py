import argparse
import asyncio
import logging
import signal
from asyncio import Event
from pathlib import Path
from types import FrameType
from typing import Any, Coroutine, List, Set

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.styles import Style

from mentat.session import Session
from mentat.session_context import SESSION_CONTEXT
from mentat.session_stream import StreamMessageSource
from mentat.terminal.output import print_stream_message
from mentat.terminal.prompt_completer import MentatCompleter
from mentat.terminal.prompt_session import MentatPromptSession


class TerminalClient:
    def __init__(
        self,
        paths: List[str] = [],
        exclude_paths: List[str] = [],
        diff: str | None = None,
        pr_diff: str | None = None,
        no_code_map: bool = False,
        use_embeddings: bool = False,
        auto_tokens: int | None = 0,
    ):
        self.paths = [Path(path) for path in paths]
        self.exclude_paths = [Path(path) for path in exclude_paths]
        self.diff = diff
        self.pr_diff = pr_diff
        self.no_code_map = no_code_map
        self.use_embeddings = use_embeddings
        self.auto_tokens = auto_tokens

        self._tasks: Set[asyncio.Task[None]] = set()
        self._should_exit = Event()

    def _create_task(self, coro: Coroutine[None, None, Any]):
        """Utility method for running a Task in the background"""

        def task_cleanup(task: asyncio.Task[None]):
            self._tasks.remove(task)

        task = asyncio.create_task(coro)
        task.add_done_callback(task_cleanup)
        self._tasks.add(task)

        return task

    async def _cprint_session_stream(self):
        async for message in self.session.stream.listen():
            print_stream_message(message)

    async def _handle_input_requests(self):
        while True:
            input_request_message = await self.session.stream.recv("input_request")
            # TODO: Make extra kwargs like plain constants
            if (
                input_request_message.extra is not None
                and input_request_message.extra.get("plain")
            ):
                prompt_session = self._plain_session
            else:
                prompt_session = self._prompt_session

            user_input = await prompt_session.prompt_async(handle_sigint=False)
            if user_input == "q":
                self._should_exit.set()
                return

            self.session.stream.send(
                user_input,
                source=StreamMessageSource.CLIENT,
                channel=f"input_request:{input_request_message.id}",
            )

    async def _listen_for_exit(self):
        await self.session.stream.recv("exit")
        self._should_exit.set()

    async def _send_session_stream_interrupt(self):
        logging.debug("Sending interrupt to session stream")
        self.session.stream.send(
            "", source=StreamMessageSource.CLIENT, channel="interrupt"
        )

    # Be careful editing this function; since we use signal.signal instead of asyncio's
    # add signal handler (which isn't available on Windows), this function can interrupt
    # asyncio coroutines, potentially causing race conditions.
    def _handle_sig_int(self, sig: int, frame: FrameType | None):
        if (
            # If session is still starting up we want to quit without an error
            not self.session
            or self.session.stream.interrupt_lock.locked() is False
        ):
            if self._should_exit.is_set():
                logging.debug("Force exiting client...")
                exit(0)
            else:
                logging.debug("Should exit client...")
                self._should_exit.set()
        else:
            # We create a task here in order to avoid race conditions
            self._create_task(self._send_session_stream_interrupt())

    def _init_signal_handlers(self):
        signal.signal(signal.SIGINT, self._handle_sig_int)

    async def _startup(self):
        self.session = Session(
            self.paths,
            self.exclude_paths,
            self.diff,
            self.pr_diff,
            self.no_code_map,
            self.use_embeddings,
            self.auto_tokens,
        )
        self.session.start()
        # Logging is setup in session.start()
        logging.debug("Running startup")

        mentat_completer = MentatCompleter()
        self._prompt_session = MentatPromptSession(completer=mentat_completer)

        plain_bindings = KeyBindings()

        @plain_bindings.add("c-c")
        @plain_bindings.add("c-d")
        def _(event: KeyPressEvent):
            if event.current_buffer.text != "":
                event.current_buffer.reset()
            else:
                event.app.exit(result="q")

        self._plain_session = PromptSession[str](
            message=[("class:prompt", ">>> ")],
            style=Style(SESSION_CONTEXT.get().config.input_style()),
            completer=None,
            key_bindings=plain_bindings,
        )

        self._create_task(mentat_completer.refresh_completions())
        self._create_task(self._cprint_session_stream())
        self._create_task(self._handle_input_requests())
        self._create_task(self._listen_for_exit())

        logging.debug("Completed startup")

    async def _shutdown(self):
        logging.debug("Running shutdown")

        # Stop session
        await self.session.stop()

        # Stop all background tasks
        for task in self._tasks:
            task.cancel()

    async def _main(self):
        logging.debug("Running main loop")
        await self._should_exit.wait()

    async def _run(self):
        self._init_signal_handlers()
        await self._startup()
        await self._main()
        await self._shutdown()

    def run(self):
        asyncio.run(self._run())


def run_cli():
    parser = argparse.ArgumentParser(
        description="Run conversation with command line args"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=[],
        help="List of file paths, directory paths, or glob patterns",
    )
    parser.add_argument(
        "--exclude",
        "-e",
        nargs="*",
        default=[],
        help="List of file paths, directory paths, or glob patterns to exclude",
    )
    parser.add_argument(
        "--diff",
        "-d",
        type=str,
        default=None,
        help="A git tree-ish (e.g. commit, branch, tag) to diff against",
    )
    parser.add_argument(
        "--pr-diff",
        "-p",
        type=str,
        default=None,
        help="A git tree-ish to diff against the latest common ancestor of",
    )
    parser.add_argument(
        "--no-code-map",
        action="store_true",
        help="Exclude the file structure/syntax map from the system prompt",
    )
    parser.add_argument(
        "--use-embeddings",
        action="store_true",
        help="Fetch/compare embeddings to auto-generate code context",
    )
    parser.add_argument(
        "--auto-tokens",
        "-a",
        type=int,
        default=0,
        help="Maximum number of auto-generated tokens to include in the prompt context",
    )
    args = parser.parse_args()
    paths = args.paths
    exclude_paths = args.exclude
    diff = args.diff
    pr_diff = args.pr_diff
    no_code_map = args.no_code_map
    use_embeddings = args.use_embeddings
    auto_tokens = args.auto_tokens

    terminal_client = TerminalClient(
        paths,
        exclude_paths,
        diff,
        pr_diff,
        no_code_map,
        use_embeddings,
        auto_tokens,
    )
    terminal_client.run()
