"""
Configuration management for SVN-Replicator.
Handles loading config files, parsing CLI arguments, and merging them intelligently.
"""

import yaml
import argparse
from pathlib import Path
from typing import Any, Dict, List, Tuple


# Define the configuration structure
# This makes it easy to add new config options in the future
# Fields:
#   - type: "str", "list", or "bool"
#   - cli: CLI argument name (e.g., "--svn-remote-uri")
#   - required: whether this field is required
#   - default: default value if not provided
CONFIG_SCHEMA = {
    "svn": {
        "remoteURI": {
            "type": "str",
            "cli": "--svn-remote-uri",
            "required": True,
            "default": None,
        },
        "skipRevisions": {
            "type": "list",
            "cli": "--skip-revisions",
            "required": False,
            "default": [],
        },
        "trunkFolder": {
            "type": "str",
            "cli": "--trunk-folder",
            "required": False,
            "default": None,
        },
        "branchFolder": {
            "type": "str",
            "cli": "--branch-folder",
            "required": False,
            "default": None,
        },
        "tagFolder": {
            "type": "str",
            "cli": "--tag-folder",
            "required": False,
            "default": None,
        },
        "ignoredFolders": {
            "type": "list",
            "cli": "--ignored-folders",
            "required": False,
            "default": [],
        },
    },
    "git": {
        "commitMsgPrefix": {
            "type": "bool",
            "cli": "--commit-msg-prefix",
            "required": False,
            "default": False,
        },
    },
    "localFiles": {
        "svnWorkingDir": {
            "type": "str",
            "cli": "--svn-working-dir",
            "required": False,
            "default": ".localWCs/svn",
        },
        "gitWorkingDir": {
            "type": "str",
            "cli": "--git-working-dir",
            "required": False,
            "default": ".localWCs/git",
        },
    },
}


def _parse_bool(value: str) -> bool:
    """Parse a boolean value from string."""
    if value.lower() in ("true", "1", "yes", "on"):
        return True
    elif value.lower() in ("false", "0", "no", "off"):
        return False
    else:
        raise argparse.ArgumentTypeError(f"Boolean value expected (true/false), got: {value}")


def _parse_comma_separated_list(value: str) -> List[str]:
    """Parse a comma-separated list."""
    return [item.strip() for item in value.split(",") if item.strip()]


def create_argument_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser based on CONFIG_SCHEMA."""
    parser = argparse.ArgumentParser(
        description="Mirror SVN repository to Git with full history preservation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Use config file (optional,defaults to config.yml)
  %(prog)s --config myconfig.yml
  
  # Override config file settings with CLI arguments
  %(prog)s --svn-remote-uri file:///path/to/repo
  
  # Use comma-separated lists
  %(prog)s --skip-revisions 1,2,3 --ignored-folders folder1,folder2
  
  # Use boolean flags
  %(prog)s --commit-msg-prefix true
        """
    )
    
    # Allow specifying a custom config file
    parser.add_argument(
        "--config",
        type=str,
        default="config.yml",
        help="Path to configuration file (default: config.yml)"
    )
    
    # Dynamically create arguments from schema
    for section, fields in CONFIG_SCHEMA.items():
        for field, field_config in fields.items():
            cli_arg = field_config.get("cli")
            if not cli_arg:
                continue
            
            dest = field
            field_type = field_config["type"]
            
            # Create argument based on type
            if field_type == "str":
                parser.add_argument(
                    cli_arg,
                    type=str,
                    dest=dest,
                    help=f"{section}.{field}"
                )
            elif field_type == "list":
                # Lists are comma-separated strings parsed by _parse_comma_separated_list
                parser.add_argument(
                    cli_arg,
                    type=_parse_comma_separated_list,
                    dest=dest,
                    help=f"{section}.{field} (comma-separated list)"
                )
            elif field_type == "bool":
                # Booleans accept true/false strings parsed by _parse_bool
                parser.add_argument(
                    cli_arg,
                    type=_parse_bool,
                    dest=dest,
                    help=f"{section}.{field} (true/false)"
                )
    
    return parser


def load_config_from_file(config_file: Path) -> Dict[str, Any]:
    """Load configuration from YAML file."""
    with open(config_file, "rb") as f:
        return yaml.safe_load(f) or {}


def merge_cli_into_config(config: Dict[str, Any], cli_args: argparse.Namespace) -> Tuple[Dict[str, Any], List[Tuple[str, Any, Any]]]:
    """
    Merge command line arguments into config, handling collisions with warnings.
    CLI arguments take precedence over config file values.
    
    Returns:
        Tuple of (merged_config, collisions_list)
        where collisions_list contains tuples of (key, old_value, new_value)
    """
    collisions = []
    
    # Check each schema field for CLI overrides
    for section, fields in CONFIG_SCHEMA.items():
        for field, field_config in fields.items():
            # Get the CLI value if it was provided
            cli_value = getattr(cli_args, field, None)
            
            # Skip if not provided in CLI (keep config file value or default)
            if cli_value is None:
                continue
            
            # Track if we're overriding a config file value
            if section in config and field in config[section]:
                collisions.append((f"{section}.{field}", config[section][field], cli_value))
            
            # Ensure section exists in config dict
            if section not in config:
                config[section] = {}
            
            # Apply CLI value (takes precedence)
            config[section][field] = cli_value
    
    return config, collisions


def print_collisions(collisions: List[Tuple[str, Any, Any]]) -> None:
    """Print warning messages for configuration collisions."""
    if collisions:
        print("[WARNING] Configuration collisions detected. Command line arguments will be used:")
        for key, old_value, new_value in collisions:
            print(f"  - {key}: config={old_value!r} -> cli={new_value!r}")


def apply_defaults(config: Dict[str, Any]) -> Dict[str, Any]:
    """Apply default values from schema for missing fields."""
    # Iterate through schema and fill in defaults for missing fields
    for section, fields in CONFIG_SCHEMA.items():
        if section not in config:
            config[section] = {}
        
        for field, field_config in fields.items():
            # Only apply default if field is not already set
            if field not in config[section]:
                default = field_config["default"]
                if default is not None:
                    config[section][field] = default
    
    return config


def load_config() -> Dict[str, Any]:
    """
    Load and merge configuration from file and CLI arguments.
    
    Returns:
        Merged configuration dictionary
    """
    # Parse CLI arguments
    parser = create_argument_parser()
    args = parser.parse_args()
    
    # Load config from YAML file
    config_file = Path(__file__).parent.parent / args.config
    
    if config_file.exists():
        config = load_config_from_file(config_file)
    else:
        print(f"[WARNING] Config file not found: {config_file}")
        config = {}
    
    # Merge CLI args into config (CLI args override file values)
    config, collisions = merge_cli_into_config(config, args)
    print_collisions(collisions)
    
    # Fill in missing fields with schema defaults
    config = apply_defaults(config)
    
    return config


def validate_config(config: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validate that the configuration has all required fields.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check required fields from schema
    for section, fields in CONFIG_SCHEMA.items():
        if section not in config:
            config[section] = {}
        
        for field, field_config in fields.items():
            # Only validate if field is marked as required
            if field_config["required"]:
                value = config[section].get(field)
                # Fail if required field is missing or empty
                if value is None or value == "":
                    cli_arg = field_config.get("cli", "")
                    return False, f"Required field {section}.{field} not provided (use {cli_arg} or set in config file)"
    
    return True, ""
