from textwrap import dedent

import pytest

from mentat.config_manager import ConfigManager, config_file_name


@pytest.mark.asyncio
async def test_config_priority(temp_testbed, mock_session_context):
    # First project config should be considered, then user config, then default config, then error
    with open(config_file_name, "w") as project_config_file:
        project_config_file.write(dedent("""\
        {
            "project-first": "project"
        }"""))

    # Since we don't want to actually change user config file or default config file,
    # we have to just mock them
    config = ConfigManager(mock_session_context.git_root, mock_session_context.stream)
    config.user_config = {"project-first": "I will not be used", "user-second": "user"}
    config.default_config = {
        "project-first": "I also am not used",
        "user-second": "Neither am I",
        "default-last": "default",
    }

    assert config._get_key("project-first") == "project"
    assert config._get_key("user-second") == "user"
    assert config._get_key("default-last") == "default"

    with pytest.raises(ValueError) as e_info:
        config._get_key("non-existent")
    assert e_info.type == ValueError


@pytest.mark.asyncio
async def test_invalid_config(temp_testbed, mock_session_context):
    # If invalid config file is found, it should use next config
    with open(config_file_name, "w") as project_config_file:
        project_config_file.write(dedent("""\
        {
            "mykey": "project",
            "invalid-json: []]
        }"""))

    config = ConfigManager(mock_session_context.git_root, mock_session_context.stream)
    config.user_config = {"mykey": "user"}
    assert config._get_key("mykey") == "user"
