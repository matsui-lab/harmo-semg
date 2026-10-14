from harmo.config.schema import validate_config
from harmo.config.validator import validate_study


def _config(data_dir, allow_empty=False):
    return {
        "definitions": {},
        "input_dir_aliases": {"raw": str(data_dir)},
        "file_mappings": [
            {
                "name": "emg",
                "base_input_dir_alias": "raw",
                "iceberg_table": "local.warehouse.raw_data",
                "input_path_template": "{subject}.csv",
                "path_patterns": {"subject": r"sub-\d+"},
                "file_format": "csv",
                "allow_empty": allow_empty,
            }
        ],
    }


def test_enabled_mapping_with_no_files_fails_validation(tmp_path):
    errors = validate_study(_config(tmp_path))
    assert any("no files matched" in error for error in errors)


def test_allow_empty_requires_explicit_boolean_opt_in(tmp_path):
    config = _config(tmp_path, allow_empty=True)
    assert validate_config(config) == []
    assert validate_study(config) == []

    config["file_mappings"][0]["allow_empty"] = "true"
    assert any("is not of type 'boolean'" in error for error in validate_config(config))
