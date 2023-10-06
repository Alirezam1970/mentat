from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

from mentat.git_handler import GIT_ROOT

from .code_context import CODE_CONTEXT, CodeContext
from .code_file import CodeFile
from .errors import MentatError
from .session_input import ask_yes_no
from .session_stream import SESSION_STREAM

if TYPE_CHECKING:
    # This normally will cause a circular import
    from .parsers.file_edit import FileEdit

CODE_FILE_MANAGER: ContextVar[CodeFileManager] = ContextVar("mentat:code_file_manager")


class CodeFileManager:
    def __init__(self):
        pass

    def read_file(self, path: Path) -> list[str]:
        git_root = GIT_ROOT.get()

        abs_path = path if path.is_absolute() else Path(git_root / path)
        with open(abs_path, "r") as f:
            lines = f.read().split("\n")
        return lines

    def read_all_file_lines(self) -> None:
        git_root = GIT_ROOT.get()
        code_context = CODE_CONTEXT.get()

        files = list(code_context.files.keys())
        self.file_lines = dict[Path, list[str]]()
        for path in files:
            # self.file_lines is relative to git root
            rel_path = Path(os.path.relpath(path, git_root))
            abs_path = Path(git_root / rel_path)
            self.file_lines[rel_path] = self.read_file(abs_path)

    def _add_file(self, abs_path: Path, code_context: CodeContext):
        logging.info(f"Adding new file {abs_path} to context")
        code_context.files[abs_path] = CodeFile(abs_path)
        # create any missing directories in the path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        with open(abs_path, "w") as f:
            f.write("")

    def _delete_file(self, abs_path: Path, code_context: CodeContext):
        logging.info(f"Deleting file {abs_path}")
        if abs_path in code_context.files:
            del code_context.files[abs_path]
        abs_path.unlink()

    # Mainly does checks on if file is in context, file exists, file is unchanged, etc.
    async def write_changes_to_files(
        self,
        file_edits: list[FileEdit],
    ):
        stream = SESSION_STREAM.get()
        git_root = GIT_ROOT.get()
        code_context = CODE_CONTEXT.get()

        for file_edit in file_edits:
            rel_path = Path(os.path.relpath(file_edit.file_path, git_root))
            if file_edit.is_creation:
                if file_edit.file_path.exists():
                    raise MentatError(
                        f"Model attempted to create file {file_edit.file_path} which"
                        " already exists"
                    )
                self._add_file(file_edit.file_path, code_context)
            else:
                if not file_edit.file_path.exists():
                    raise MentatError(
                        f"Attempted to edit non-existent file {file_edit.file_path}"
                    )
                elif file_edit.file_path not in code_context.files:
                    raise MentatError(
                        f"Attempted to edit file {file_edit.file_path} not in context"
                    )

            if file_edit.is_deletion:
                await stream.send(
                    f"Are you sure you want to delete {rel_path}?", color="red"
                )
                if await ask_yes_no(default_yes=False):
                    await stream.send(f"Deleting {rel_path}...", color="red")
                    self._delete_file(file_edit.file_path, code_context)
                    continue
                else:
                    await stream.send(f"Not deleting {rel_path}", color="green")

            if not file_edit.is_creation:
                stored_lines = self.file_lines[rel_path]
                if stored_lines != self.read_file(rel_path):
                    logging.info(
                        f"File '{file_edit.file_path}' changed while generating changes"
                    )
                    await stream.send(
                        f"File '{rel_path}' changed while generating; current"
                        " file changes will be erased. Continue?",
                        color="light_yellow",
                    )
                    if not await ask_yes_no(default_yes=False):
                        await stream.send(f"Not applying changes to file {rel_path}")
            else:
                stored_lines = []

            if file_edit.rename_file_path is not None:
                if file_edit.rename_file_path.exists():
                    raise MentatError(
                        f"Attempted to rename file {file_edit.file_path} to existing"
                        f" file {file_edit.rename_file_path}"
                    )
                self._add_file(file_edit.rename_file_path, code_context)
                self._delete_file(file_edit.file_path, code_context)
                file_edit.file_path = file_edit.rename_file_path

            new_lines = file_edit.get_updated_file_lines(stored_lines)
            with open(file_edit.file_path, "w") as f:
                f.write("\n".join(new_lines))
