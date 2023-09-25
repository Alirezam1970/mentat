import json
from enum import Enum
from pathlib import Path
from typing import Any

from typing_extensions import override

from mentat.code_file_manager import CodeFileManager
from mentat.config_manager import ConfigManager
from mentat.parsers.change_display_helper import DisplayInformation, FileActionType
from mentat.parsers.file_edit import FileEdit, Replacement
from mentat.parsers.parser import Parser
from mentat.prompts.prompts import read_prompt

block_parser_prompt_filename = Path("block_parser_prompt.txt")


class _BlockParserAction(Enum):
    Insert = "insert"
    Replace = "replace"
    Delete = "delete"
    CreateFile = "create-file"
    DeleteFile = "delete-file"
    RenameFile = "rename-file"


class _BlockParserIndicator(Enum):
    Start = "@@start"
    Code = "@@code"
    End = "@@end"


class _BlockParserJsonKeys(Enum):
    File = "file"
    Action = "action"
    Name = "name"
    BeforeLine = "insert-before-line"
    AfterLine = "insert-after-line"
    StartLine = "start-line"
    EndLine = "end-line"


class _BlockParserDeserializedJson:
    def __init__(self, json_data: dict[str, Any]):
        self.file = json_data.get(_BlockParserJsonKeys.File.value, None)
        self.action = json_data.get(_BlockParserJsonKeys.Action.value, None)
        self.name = json_data.get(_BlockParserJsonKeys.Name.value, None)
        self.before_line = json_data.get(_BlockParserJsonKeys.BeforeLine.value, None)
        self.after_line = json_data.get(_BlockParserJsonKeys.AfterLine.value, None)
        self.start_line = json_data.get(_BlockParserJsonKeys.StartLine.value, None)
        self.end_line = json_data.get(_BlockParserJsonKeys.EndLine.value, None)

        if self.file is not None:
            self.file = Path(self.file)
        if self.action is not None:
            self.action: _BlockParserAction | None = _BlockParserAction(self.action)
        if self.name is not None:
            self.name = Path(self.name)
        if self.before_line is not None:
            self.before_line = int(self.before_line)
        if self.after_line is not None:
            self.after_line = int(self.after_line)
        if self.start_line is not None:
            self.start_line = int(self.start_line)
        if self.end_line is not None:
            self.end_line = int(self.end_line)


class BlockParser(Parser):
    @override
    def get_system_prompt(self) -> str:
        return read_prompt(block_parser_prompt_filename)

    @override
    def _could_be_special(self, cur_line: str) -> bool:
        return any(
            to_match.value.startswith(cur_line) for to_match in _BlockParserIndicator
        )

    @override
    def _starts_special(self, line: str) -> bool:
        return line == _BlockParserIndicator.Start.value

    @override
    def _ends_special(self, line: str) -> bool:
        return (
            line == _BlockParserIndicator.Code.value
            or line == _BlockParserIndicator.End.value
        )

    @override
    def _special_block(
        self,
        code_file_manager: CodeFileManager,
        config: ConfigManager,
        rename_map: dict[Path, Path],
        special_block: str,
    ) -> tuple[DisplayInformation, FileEdit, bool]:
        block = special_block.strip().split("\n")
        json_lines = block[1:-1]
        # TODO: json error
        json_data: dict[str, Any] = json.loads("\n".join(json_lines))
        deserialized_json = _BlockParserDeserializedJson(json_data)

        if deserialized_json.action is None:
            raise ValueError()

        starting_line = 0
        ending_line = 0
        match deserialized_json.action:
            case _BlockParserAction.Insert:
                if deserialized_json.before_line is not None:
                    starting_line = deserialized_json.before_line - 1
                    if (
                        deserialized_json.after_line is not None
                        and starting_line != deserialized_json.after_line
                    ):
                        raise ValueError("Insert line numbers invalid")
                elif deserialized_json.after_line is not None:
                    starting_line = deserialized_json.after_line
                else:
                    raise ValueError("Insert line number not specified")
                ending_line = starting_line
                file_action = FileActionType.UpdateFile

            case _BlockParserAction.Replace | _BlockParserAction.Delete:
                starting_line = deserialized_json.start_line - 1
                ending_line = deserialized_json.end_line
                file_action = FileActionType.UpdateFile

            case _BlockParserAction.CreateFile:
                file_action = FileActionType.CreateFile

            case _BlockParserAction.DeleteFile:
                file_action = FileActionType.DeleteFile

            case _BlockParserAction.RenameFile:
                file_action = FileActionType.RenameFile

        file_lines = (
            []
            if file_action == FileActionType.CreateFile
            else code_file_manager.file_lines[
                rename_map.get(
                    config.git_root / deserialized_json.file,
                    config.git_root / deserialized_json.file,
                ).relative_to(config.git_root)
            ]
        )
        display_information = DisplayInformation(
            deserialized_json.file,
            file_lines,
            [],
            file_lines[starting_line:ending_line],
            file_action,
            starting_line,
            ending_line,
            deserialized_json.name,
        )

        replacements = list[Replacement]()
        if deserialized_json.action == _BlockParserAction.Delete:
            replacements.append(Replacement(starting_line, ending_line, []))
        file_edit = FileEdit(
            config.git_root / deserialized_json.file,
            replacements,
            is_creation=file_action == FileActionType.CreateFile,
            is_deletion=file_action == FileActionType.DeleteFile,
            rename_file_path=deserialized_json.name,
        )
        has_code = block[-1] == _BlockParserIndicator.Code.value
        return (display_information, file_edit, has_code)

    @override
    def _ends_code(self, line: str) -> bool:
        return line == _BlockParserIndicator.End.value

    @override
    def _add_code_block(
        self,
        special_block: str,
        code_block: str,
        display_information: DisplayInformation,
        file_edit: FileEdit,
    ):
        file_edit.replacements.append(
            Replacement(
                display_information.first_changed_line,
                display_information.last_changed_line,
                code_block.split("\n")[:-2],
            )
        )
