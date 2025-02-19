from __future__ import annotations

import json
import logging
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Optional, cast

from mentat.session_stream import SessionStream
from mentat.utils import fetch_resource, mentat_dir_path

default_config_file_name = Path("default_config.json")
config_file_name = Path(".mentat_config.json")
user_config_path = mentat_dir_path / config_file_name


class ConfigManager:
    def __init__(self, git_root: Path, stream: SessionStream):
        if user_config_path.exists():
            with open(user_config_path) as config_file:
                try:
                    self.user_config = json.load(config_file)
                except JSONDecodeError:
                    logging.info("User config file contains invalid json")
                    stream.send(
                        "Warning: User .mentat_config.json contains invalid"
                        " json; ignoring user configuration file",
                        color="light_yellow",
                    )
                    self.user_config = dict[str, str]()
        else:
            self.user_config = dict[str, str]()

        project_config_path = git_root / config_file_name
        if project_config_path.exists():
            with open(project_config_path) as config_file:
                try:
                    self.project_config = json.load(config_file)
                except JSONDecodeError:
                    logging.info("Project config file contains invalid json")
                    stream.send(
                        "Warning: Git project .mentat_config.json contains invalid"
                        " json; ignoring project configuration file",
                        color="light_yellow",
                    )
                    self.project_config = dict[str, str]()
        else:
            self.project_config = dict[str, str]()

        default_config_path = fetch_resource(default_config_file_name)
        with default_config_path.open("r") as config_file:
            self.default_config = json.load(config_file)

    def input_style(self) -> list[tuple[str, str]]:
        return cast(list[tuple[str, str]], self._get_key("input-style"))

    def model(self) -> str:
        return cast(str, self._get_key("model"))

    def maximum_context(self) -> Optional[int]:
        maximum_context = self._get_key("maximum-context")
        if maximum_context:
            return int(maximum_context)
        return None

    def file_exclude_glob_list(self) -> list[str]:
        return cast(list[str], self._get_key("file-exclude-glob-list"))

    def parser(self) -> str:
        return cast(str, self._get_key("format"))

    def _get_key(self, key: str) -> Any:
        if key in self.project_config:
            return self.project_config[key]
        elif key in self.user_config:
            return self.user_config[key]
        elif key in self.default_config:
            return self.default_config[key]
        else:
            raise ValueError(f"No value for config key {key} found")
