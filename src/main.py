from config_manager import load_config, validate_config
from processor import process_svn_to_git


def main():
    """Main entry point for the application."""
    # Load and merge configuration
    config = load_config()
    
    # Validate configuration
    is_valid, error_msg = validate_config(config)
    if not is_valid:
        print(f"[ERROR] {error_msg}")
        return 1
    
    # Process the repository
    try:
        process_svn_to_git(config)
        return 0
    except Exception as e:
        print(f"[ERROR] Processing failed: {e}")
        return 1

if __name__ == "__main__":
    exit(main())