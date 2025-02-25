import os
import shutil
from pathlib import Path


class ObsidianVaultCopier:
    # Files and paths to be explicitly copied (relative to repository root)
    # Can include files from any location in the vault
    CONFIG_FILES = [
        # Common Obsidian files you might want to explicitly include
        'README.md',
        'home.md',
        'index.md',
        'MOC.md',  # Map of Content
        # Add any specific files you want to ensure are copied
    ]

    # File extensions to copy from the project
    NOTE_EXTENSIONS = {'.md', '.canvas', '.excalidraw'}
    
    # Other assets that might be referenced in notes
    ASSET_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.pdf', '.mp3', '.mp4', '.webm'}
    
    # All extensions to copy
    ALL_EXTENSIONS = NOTE_EXTENSIONS.union(ASSET_EXTENSIONS)

    # Directories to exclude from copying
    EXCLUDE_DIRS = {'.obsidian', '.smart-env', '.trash', '_templates', '.git', '.github'}


    def __init__(self, vault_root: str, dest_path: str, subfolder: str = None):
        self.vault_root = Path(vault_root)
        self.dest_path = Path(dest_path)
        
        # Set the source path (either the entire vault or a specific subfolder)
        if subfolder:
            self.source_path = self.vault_root / subfolder
        else:
            self.source_path = self.vault_root


    def copy_vault(self):
        """
        Main method to copy the Obsidian vault or a specific subfolder.
        """
        source_desc = f"subfolder '{self.source_path.relative_to(self.vault_root)}'" if self.source_path != self.vault_root else "entire vault"
        print(f"Copying Obsidian {source_desc} from {self.vault_root} to {self.dest_path}")

        # Create destination directory if it doesn't exist
        self.dest_path.mkdir(parents=True, exist_ok=True)

        # Copy explicitly specified files
        self._copy_config_files()

        # Copy all relevant files from vault/subfolder
        self._copy_vault_files()

        print("Copy completed!")


    def _copy_config_files(self):
        """
        Copy files specified in CONFIG_FILES from anywhere in the vault.
        Paths in CONFIG_FILES should be relative to the vault root.
        """
        print("Copying specified files...")
        for config_file in self.CONFIG_FILES:
            source_file = self.vault_root / config_file
            if source_file.exists():
                # Create a flattened filename for the destination
                flat_filename = Path(config_file).name
                
                # If we want to preserve the directory structure in the filename
                # (e.g., folder_note.md instead of just note.md)
                if len(Path(config_file).parts) > 1:
                    flat_filename = "_".join(Path(config_file).parts)
                
                dest_file = self.dest_path / flat_filename
                print(f"Copying {config_file} as {flat_filename}")
                shutil.copy2(source_file, dest_file)
            else:
                print(f"Warning: File {config_file} not found")


    def _copy_vault_files(self):
        """
        Copy all relevant Obsidian files into a flat structure.
        Starting from either the vault root or a specific subfolder.
        """
        if not self.source_path.exists():
            print(f"Error: source directory {self.source_path} not found!")
            return
        
        print(f"Copying files from {self.source_path.relative_to(self.vault_root)}...")
        file_count = 0

        # Walk through the source directory
        for root, dirs, files in os.walk(self.source_path):
            # Skip excluded directories
            dirs[:] = [d for d in dirs if d not in self.EXCLUDE_DIRS]

            current_dir = Path(root)

            # Copy files with matching extensions
            for file in files:
                file_path = current_dir / file
                if file_path.suffix in self.ALL_EXTENSIONS:
                    # Calculate relative path - from vault root for full search,
                    # or from subfolder when a specific subfolder is being copied
                    if self.source_path != self.vault_root:
                        # For subfolder-specific search, make relative to the subfolder
                        rel_path = file_path.relative_to(self.source_path)
                        # For logging, show the full path relative to vault root
                        log_path = file_path.relative_to(self.vault_root)
                    else:
                        # For full vault search, make relative to vault root
                        rel_path = file_path.relative_to(self.vault_root)
                        log_path = rel_path

                    # Skip files in excluded directories (deeper check)
                    if any(excluded in str(log_path.parts) for excluded in self.EXCLUDE_DIRS):
                        continue

                    # Create a flattened filename that preserves the original path structure
                    # (but without the subfolder name when a subfolder is specified)
                    unique_filename = ".".join(rel_path.parts)
                    dest_file = self.dest_path / unique_filename
                    
                    print(f"Copying {log_path} as {unique_filename}")
                    shutil.copy2(file_path, dest_file)
                    file_count += 1

        print(f"Total files copied: {file_count}")


# Get the script's directory
script_dir = Path(__file__).parent

# Add the copy path here
copy_path = 'C:/Users/<YOUR_USER>/Downloads/'

# Define paths
vault_root = script_dir  # Assuming the script is at the vault root
dest_path = copy_path + 'extracted_obsidian_vault'

# Specify a subfolder to focus on (set to None to copy the entire vault)
subfolder = "Project X"  # Change this to your specific project folder or set to None

# Create and run the copier
try:
    copier = ObsidianVaultCopier(vault_root, dest_path, subfolder)
    copier.copy_vault()
except Exception as e:
    print(f"Error: {str(e)}")
