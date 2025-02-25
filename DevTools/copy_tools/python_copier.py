import os
import shutil
from dotenv import load_dotenv
from pathlib import Path


class PythonProjectCopier:
    # Files and paths to be copied (relative to repository root)
    # Can include files from any location in the repository
    CONFIG_FILES = [
        'requirements.txt',  # Files at repository root
        'setup.py',
        'pyproject.toml',
        '.env.example',
        'README.md',
        'table_detection.py',  # Files in subdirectories
        # Add any other files you want to copy (use relative paths)
    ]

    # File extensions to copy from the project
    CODE_EXTENSIONS = {'.py', '.json', '.yaml', '.yml', '.sql'}

    # Directories to exclude
    EXCLUDE_DIRS = {'.git', '.venv', 'venv', '__pycache__', '.idea', '.pytest_cache', 'logs'}


    def __init__(self, repo_root: str, src_path: str, dest_path: str):
        self.repo_root = Path(repo_root)
        self.source_path = Path(src_path)
        self.dest_path = Path(dest_path)


    def copy_project(self):
        """
        Main method to copy the Python project.
        """
        print(f"Copying Python project from {self.source_path} to {self.dest_path}")

        # Create destination directory if it doesn't exist
        self.dest_path.mkdir(parents=True, exist_ok=True)

        # Copy configuration files
        self._copy_config_files()

        # Copy Python source files
        self._copy_source_files()

        print("Project copy completed!")


    def _copy_config_files(self):
        """
        Copy files specified in CONFIG_FILES from anywhere in the repository.
        Paths in CONFIG_FILES should be relative to the repository root.
        """
        print("Copying specified files...")
        for config_file in self.CONFIG_FILES:
            source_file = self.repo_root / config_file
            if source_file.exists():
                # Create a flattened filename for the destination
                flat_filename = Path(config_file).name
                dest_file = self.dest_path / flat_filename

                # If we want to preserve the directory structure in the filename
                # (e.g., src_table_detection.py instead of just table_detection.py)
                if len(Path(config_file).parts) > 1:
                    flat_filename = "_".join(Path(config_file).parts)
                    dest_file = self.dest_path / flat_filename

                print(f"Copying {config_file} as {flat_filename}")
                shutil.copy2(source_file, dest_file)
            else:
                print(f"Warning: File {config_file} not found")


    def _copy_source_files(self):
        """
        Copy all relevant Python files into a flat structure.
        """
        if not self.source_path.exists():
            print("Error: source directory not found!")
            return
        print("Copying source files...")

        # Walk through the project directory
        for root, dirs, files in os.walk(self.source_path):
            # Skip excluded directories
            dirs[:] = [d for d in dirs if d not in self.EXCLUDE_DIRS]

            current_dir = Path(root)

            # Copy files with matching extensions
            for file in files:
                file_path = current_dir / file
                if file_path.suffix in self.CODE_EXTENSIONS:
                    # Create a unique filename by joining the relative path parts
                    rel_path = file_path.relative_to(self.source_path)

                    # Skip files in excluded directories (deeper check)
                    if any(excluded in str(rel_path) for excluded in self.EXCLUDE_DIRS):
                        continue

                    unique_filename = ".".join(rel_path.parts)
                    dest_file = self.dest_path / unique_filename
                    print(f"Copying {rel_path} as {unique_filename}")
                    shutil.copy2(file_path, dest_file)

# Load environment variables
load_dotenv()

# Get the script's directory
script_dir = Path(__file__).parent

# Get the copy path from env or use a default
copy_path = Path(os.getenv('COPY_PATH', '.'))

# Define paths
repo_root = script_dir  # Assuming the script is at the repository root
src_path = repo_root / 'src'  # Source path for recursive search of Python files
dest_path = copy_path / 'extracted_python_project_files'

# Create and run the copier
try:
    copier = PythonProjectCopier(repo_root, src_path, dest_path)
    copier.copy_project()
except Exception as e:
    print(f"Error: {str(e)}")
