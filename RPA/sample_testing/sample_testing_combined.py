import os
import csv
import random
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
import json
from collections import OrderedDict, defaultdict


class ColumnType:
    """Enum for column data types"""
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    UNKNOWN = "unknown"


class DimensionalFilter:
    """A global filter for a single column (dimension)"""
    def __init__(self, column="", column_type=ColumnType.TEXT):
        self.column = column
        self.column_type = column_type
        self.filter_config = {}

    def to_dict(self):
        return {
            'column': self.column,
            'column_type': self.column_type,
            'filter_config': self.filter_config
        }

    def from_dict(self, data):
        self.column = data.get('column', '')
        self.column_type = data.get('column_type', ColumnType.TEXT)
        self.filter_config = data.get('filter_config', {})
        return self

    def apply_filter(self, data):
        """Apply this dimensional filter to the data"""
        if not self.column or not self.filter_config:
            return data

        filtered = []
        for row in data:
            if self.matches(row):
                filtered.append(row)
        return filtered

    def matches(self, row):
        """Check if a row matches this filter"""
        value = row.get(self.column)

        if self.column_type == ColumnType.TEXT:
            filter_type = self.filter_config.get('type', 'equals')
            if filter_type == 'equals':
                values = self.filter_config.get('values', [])
                return not values or str(value) in values
            elif filter_type == 'contains':
                pattern = self.filter_config.get('pattern', '')
                return not pattern or pattern.lower() in str(value).lower()

        elif self.column_type == ColumnType.NUMBER:
            if value is None:
                return False
            min_val = self.filter_config.get('min')
            max_val = self.filter_config.get('max')
            if min_val is not None and value < min_val:
                return False
            if max_val is not None and value > max_val:
                return False
            return True

        elif self.column_type == ColumnType.DATE:
            if value is None:
                return False
            date_from = self.filter_config.get('from')
            date_to = self.filter_config.get('to')
            if date_from and value < date_from:
                return False
            if date_to and value > date_to:
                return False
            return True

        return True

    def get_description(self):
        """Get a human-readable description of this filter"""
        if not self.filter_config:
            return f"{self.column}: No filter"

        if self.column_type == ColumnType.TEXT:
            filter_type = self.filter_config.get('type', 'equals')
            if filter_type == 'equals':
                values = self.filter_config.get('values', [])
                if values:
                    return f"{self.column} = {', '.join(values[:3])}{'...' if len(values) > 3 else ''}"
            elif filter_type == 'contains':
                pattern = self.filter_config.get('pattern', '')
                if pattern:
                    return f"{self.column} contains '{pattern}'"

        elif self.column_type == ColumnType.NUMBER:
            parts = []
            if self.filter_config.get('min') is not None:
                parts.append(f">= {self.filter_config['min']:,.2f}")
            if self.filter_config.get('max') is not None:
                parts.append(f"<= {self.filter_config['max']:,.2f}")
            if parts:
                return f"{self.column} {' and '.join(parts)}"

        elif self.column_type == ColumnType.DATE:
            parts = []
            if self.filter_config.get('from'):
                parts.append(f"from {self.filter_config['from'].strftime('%d-%m-%Y')}")
            if self.filter_config.get('to'):
                parts.append(f"to {self.filter_config['to'].strftime('%d-%m-%Y')}")
            if parts:
                return f"{self.column} {' '.join(parts)}"

        return f"{self.column}: No filter"


class SamplingRule:
    """A sampling rule with specific quota requirements"""
    def __init__(self, name="", column="", column_type=ColumnType.TEXT):
        self.name = name
        self.column = column
        self.column_type = column_type
        self.filter_config = {}
        self.sample_count = 5

    def to_dict(self):
        return {
            'name': self.name,
            'column': self.column,
            'column_type': self.column_type,
            'filter_config': self.filter_config,
            'sample_count': self.sample_count
        }

    def from_dict(self, data):
        self.name = data.get('name', '')
        self.column = data.get('column', '')
        self.column_type = data.get('column_type', ColumnType.TEXT)
        self.filter_config = data.get('filter_config', {})
        self.sample_count = data.get('sample_count', 5)
        return self

    def matches(self, row):
        """Check if a row matches this sampling rule"""
        # Same logic as DimensionalFilter.matches
        value = row.get(self.column)

        if self.column_type == ColumnType.TEXT:
            filter_type = self.filter_config.get('type', 'equals')
            if filter_type == 'equals':
                values = self.filter_config.get('values', [])
                return not values or str(value) in values
            elif filter_type == 'contains':
                pattern = self.filter_config.get('pattern', '')
                return not pattern or pattern.lower() in str(value).lower()

        elif self.column_type == ColumnType.NUMBER:
            if value is None:
                return False
            min_val = self.filter_config.get('min')
            max_val = self.filter_config.get('max')
            if min_val is not None and value < min_val:
                return False
            if max_val is not None and value > max_val:
                return False
            return True

        elif self.column_type == ColumnType.DATE:
            if value is None:
                return False
            date_from = self.filter_config.get('from')
            date_to = self.filter_config.get('to')
            if date_from and value < date_from:
                return False
            if date_to and value > date_to:
                return False
            return True

        return True

    def get_description(self):
        """Get description of the rule criteria"""
        # Similar to DimensionalFilter but simpler
        if not self.filter_config:
            return "No criteria"

        if self.column_type == ColumnType.TEXT:
            filter_type = self.filter_config.get('type', 'equals')
            if filter_type == 'equals':
                values = self.filter_config.get('values', [])
                if values:
                    return f"{self.column} = {', '.join(values[:3])}{'...' if len(values) > 3 else ''}"
            elif filter_type == 'contains':
                pattern = self.filter_config.get('pattern', '')
                if pattern:
                    return f"{self.column} contains '{pattern}'"

        elif self.column_type == ColumnType.NUMBER:
            parts = []
            if self.filter_config.get('min') is not None:
                parts.append(f">= {self.filter_config['min']:,.2f}")
            if self.filter_config.get('max') is not None:
                parts.append(f"<= {self.filter_config['max']:,.2f}")
            if parts:
                return f"{self.column} {' and '.join(parts)}"

        elif self.column_type == ColumnType.DATE:
            parts = []
            if self.filter_config.get('from'):
                parts.append(f"from {self.filter_config['from'].strftime('%d-%m-%Y')}")
            if self.filter_config.get('to'):
                parts.append(f"to {self.filter_config['to'].strftime('%d-%m-%Y')}")
            if parts:
                return f"{self.column} {' '.join(parts)}"

        return "No criteria"


class HybridSampleTestingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Hybrid Dimensional & Stratified Sampling Tool")
        self.root.geometry("1300x800")

        # Configure styles
        style = ttk.Style()
        style.configure('Accent.TButton', font=('TkDefaultFont', 11, 'bold'))

        # Data storage
        self.data = []
        self.filtered_data = []
        self.column_names = []
        self.column_types = {}
        self.global_filters = []  # List of DimensionalFilter objects
        self.sampling_rules = []  # List of SamplingRule objects
        self.results = []

        # CSV parsing settings
        self.delimiter = ';'
        self.encoding = 'utf-8'

        # Create main frames
        self.create_widgets()

    def create_widgets(self):
        # Create notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=5, pady=5)

        # Tab 1: Data Loading
        self.data_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.data_tab, text="Data Loading")
        self.create_data_tab()

        # Tab 2: Global Filters
        self.filters_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.filters_tab, text="Global Filters")
        self.create_filters_tab()

        # Tab 3: Sampling Rules
        self.rules_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.rules_tab, text="Sampling Rules")
        self.create_rules_tab()

        # Tab 4: Results
        self.results_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.results_tab, text="Sample Results")
        self.create_results_tab()

        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

    def create_data_tab(self):
        # File selection frame
        file_frame = ttk.Frame(self.data_tab, padding="10")
        file_frame.grid(row=0, column=0, sticky=(tk.W, tk.E))

        ttk.Label(file_frame, text="CSV File:").grid(row=0, column=0, sticky=tk.W)
        self.file_label = ttk.Label(file_frame, text="No file selected", relief=tk.SUNKEN, width=50)
        self.file_label.grid(row=0, column=1, padx=5, sticky=(tk.W, tk.E))
        ttk.Button(file_frame, text="Browse", command=self.load_file).grid(row=0, column=2)

        # CSV settings
        ttk.Label(file_frame, text="Delimiter:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.delimiter_var = tk.StringVar(value=';')
        delimiter_combo = ttk.Combobox(file_frame, textvariable=self.delimiter_var,
                                       values=[';', ',', '\t', '|'], width=5)
        delimiter_combo.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)

        # Column info frame
        info_frame = ttk.LabelFrame(self.data_tab, text="Detected Columns", padding="10")
        info_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        # Column list
        self.column_listbox = tk.Listbox(info_frame, height=10)
        column_scroll = ttk.Scrollbar(info_frame, orient=tk.VERTICAL, command=self.column_listbox.yview)
        self.column_listbox.configure(yscrollcommand=column_scroll.set)

        self.column_listbox.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        column_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # Data preview
        preview_frame = ttk.LabelFrame(self.data_tab, text="Data Preview", padding="10")
        preview_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        # Create preview tree
        self.preview_tree = ttk.Treeview(preview_frame, show='headings', height=10)
        preview_scroll = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL, command=self.preview_tree.yview)
        self.preview_tree.configure(yscrollcommand=preview_scroll.set)

        self.preview_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        preview_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # Info labels
        self.info_label = ttk.Label(preview_frame, text="")
        self.info_label.grid(row=1, column=0, columnspan=2, pady=5)

        # Configure grid
        self.data_tab.columnconfigure(0, weight=1)
        self.data_tab.rowconfigure(2, weight=1)
        info_frame.columnconfigure(0, weight=1)
        info_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)

    def create_filters_tab(self):
        # Instructions
        inst_frame = ttk.Frame(self.filters_tab, padding="10")
        inst_frame.grid(row=0, column=0, sticky=(tk.W, tk.E))

        instructions = ttk.Label(inst_frame,
                                 text="Global filters apply to ALL data before sampling rules are applied.\n" +
                                      "Example: Set Year = 2024 here, then all sampling rules will only consider 2024 data.",
                                 font=('TkDefaultFont', 9, 'italic'),
                                 wraplength=800)
        instructions.grid(row=0, column=0, pady=5)

        # Button frame
        button_frame = ttk.Frame(self.filters_tab, padding="10")
        button_frame.grid(row=1, column=0, sticky=(tk.W, tk.E))

        ttk.Button(button_frame, text="Add Global Filter", command=self.add_global_filter).grid(row=0, column=0, padx=5)
        ttk.Button(button_frame, text="Edit Filter", command=self.edit_global_filter).grid(row=0, column=1, padx=5)
        ttk.Button(button_frame, text="Delete Filter", command=self.delete_global_filter).grid(row=0, column=2, padx=5)
        ttk.Button(button_frame, text="Clear All", command=self.clear_global_filters).grid(row=0, column=3, padx=5)

        # Filters list
        filters_frame = ttk.LabelFrame(self.filters_tab, text="Active Global Filters", padding="10")
        filters_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        columns = ('Column', 'Type', 'Filter')
        self.filters_tree = ttk.Treeview(filters_frame, columns=columns, show='headings', height=10)

        self.filters_tree.heading('Column', text='Column')
        self.filters_tree.heading('Type', text='Data Type')
        self.filters_tree.heading('Filter', text='Filter Criteria')

        self.filters_tree.column('Column', width=200)
        self.filters_tree.column('Type', width=100)
        self.filters_tree.column('Filter', width=400)

        filters_scroll = ttk.Scrollbar(filters_frame, orient=tk.VERTICAL, command=self.filters_tree.yview)
        self.filters_tree.configure(yscrollcommand=filters_scroll.set)

        self.filters_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        filters_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # Status frame
        status_frame = ttk.Frame(self.filters_tab, padding="10")
        status_frame.grid(row=3, column=0, sticky=(tk.W, tk.E))

        self.global_filtered_label = ttk.Label(status_frame, text="Total records: 0 | After global filters: 0")
        self.global_filtered_label.grid(row=0, column=0)

        ttk.Button(status_frame, text="🔍 Apply Global Filters",
                   command=self.apply_global_filters).grid(row=0, column=1, padx=20)

        # Configure grid
        self.filters_tab.columnconfigure(0, weight=1)
        self.filters_tab.rowconfigure(2, weight=1)
        filters_frame.columnconfigure(0, weight=1)
        filters_frame.rowconfigure(0, weight=1)

    def create_rules_tab(self):
        # Instructions
        inst_frame = ttk.Frame(self.rules_tab, padding="10")
        inst_frame.grid(row=0, column=0, sticky=(tk.W, tk.E))

        instructions = ttk.Label(inst_frame,
                                 text="Sampling rules specify how many samples to take from specific criteria.\n" +
                                      "Example: '5 samples where Legal Form = GmbH', '3 samples where Legal Form = AG'\n" +
                                      "Rules are applied to data that passes global filters.",
                                 font=('TkDefaultFont', 9, 'italic'),
                                 wraplength=800)
        instructions.grid(row=0, column=0, pady=5)

        # Button frame
        button_frame = ttk.Frame(self.rules_tab, padding="10")
        button_frame.grid(row=1, column=0, sticky=(tk.W, tk.E))

        ttk.Button(button_frame, text="Add Sampling Rule", command=self.add_sampling_rule).grid(row=0, column=0, padx=5)
        ttk.Button(button_frame, text="Edit Rule", command=self.edit_sampling_rule).grid(row=0, column=1, padx=5)
        ttk.Button(button_frame, text="Delete Rule", command=self.delete_sampling_rule).grid(row=0, column=2, padx=5)
        ttk.Button(button_frame, text="Clear All", command=self.clear_sampling_rules).grid(row=0, column=3, padx=5)
        ttk.Button(button_frame, text="Save Configuration", command=self.save_configuration).grid(row=0, column=4, padx=5)
        ttk.Button(button_frame, text="Load Configuration", command=self.load_configuration).grid(row=0, column=5, padx=5)

        # Rules list
        rules_frame = ttk.LabelFrame(self.rules_tab, text="Sampling Rules with Quotas", padding="10")
        rules_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        columns = ('Rule Name', 'Criteria', 'Sample Count', 'Available')
        self.rules_tree = ttk.Treeview(rules_frame, columns=columns, show='headings', height=10)

        self.rules_tree.heading('Rule Name', text='Rule Name')
        self.rules_tree.heading('Criteria', text='Sampling Criteria')
        self.rules_tree.heading('Sample Count', text='Samples Required')
        self.rules_tree.heading('Available', text='Available Records')

        self.rules_tree.column('Rule Name', width=200)
        self.rules_tree.column('Criteria', width=350)
        self.rules_tree.column('Sample Count', width=120)
        self.rules_tree.column('Available', width=120)

        rules_scroll = ttk.Scrollbar(rules_frame, orient=tk.VERTICAL, command=self.rules_tree.yview)
        self.rules_tree.configure(yscrollcommand=rules_scroll.set)

        self.rules_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        rules_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # Generate button
        generate_frame = ttk.Frame(self.rules_tab, padding="10")
        generate_frame.grid(row=3, column=0, sticky=(tk.W, tk.E))

        self.total_required_label = ttk.Label(generate_frame, text="Total samples required: 0")
        self.total_required_label.grid(row=0, column=0, padx=10)

        ttk.Button(generate_frame, text="🎯 Generate Stratified Sample",
                   command=self.generate_stratified_sample,
                   style='Accent.TButton').grid(row=0, column=1, padx=10)

        self.progress_label = ttk.Label(generate_frame, text="")
        self.progress_label.grid(row=0, column=2, padx=10)

        # Configure grid
        self.rules_tab.columnconfigure(0, weight=1)
        self.rules_tab.rowconfigure(2, weight=1)
        rules_frame.columnconfigure(0, weight=1)
        rules_frame.rowconfigure(0, weight=1)

    def create_results_tab(self):
        # Results summary
        summary_frame = ttk.Frame(self.results_tab, padding="10")
        summary_frame.grid(row=0, column=0, sticky=(tk.W, tk.E))

        self.results_summary_label = ttk.Label(summary_frame, text="No sample generated yet")
        self.results_summary_label.grid(row=0, column=0)

        # Results tree
        results_frame = ttk.LabelFrame(self.results_tab, text="Sample Results", padding="10")
        results_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        self.results_tree = ttk.Treeview(results_frame, show='headings', height=15)
        results_scroll = ttk.Scrollbar(results_frame, orient=tk.VERTICAL, command=self.results_tree.yview)
        self.results_tree.configure(yscrollcommand=results_scroll.set)

        self.results_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        results_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # Export buttons
        export_frame = ttk.Frame(self.results_tab, padding="10")
        export_frame.grid(row=2, column=0, sticky=(tk.W, tk.E))

        ttk.Button(export_frame, text="Export Sample to CSV", command=self.export_results).grid(row=0, column=0, padx=5)
        ttk.Button(export_frame, text="Export by Rule", command=self.export_by_rule).grid(row=0, column=1, padx=5)
        ttk.Button(export_frame, text="Clear Results", command=self.clear_results).grid(row=0, column=2, padx=5)

        # Configure grid
        self.results_tab.columnconfigure(0, weight=1)
        self.results_tab.rowconfigure(1, weight=1)
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)

    def detect_column_type(self, values):
        """Detect the type of a column based on its values"""
        if not values:
            return ColumnType.UNKNOWN

        # Try to parse as dates
        date_formats = [
            '%d-%m-%Y', '%d/%m/%Y', '%d.%m.%Y',  # European formats
            '%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d',  # ISO formats
            '%m/%d/%Y', '%m-%d-%Y', '%m.%d.%Y',  # US formats
        ]
        date_count = 0
        for value in values[:20]:  # Check first 20 values
            if not value:
                continue
            for fmt in date_formats:
                try:
                    datetime.strptime(value, fmt)
                    date_count += 1
                    break
                except:
                    pass

        if date_count > len(values[:20]) * 0.5:  # More than 50% parsed as dates
            return ColumnType.DATE

        # Try to parse as numbers
        number_count = 0
        for value in values[:20]:
            if not value:
                continue
            try:
                # Try both comma and dot as decimal separator
                float(value.replace(',', '.').replace(' ', ''))
                number_count += 1
            except:
                pass

        if number_count > len(values[:20]) * 0.5:  # More than 50% parsed as numbers
            return ColumnType.NUMBER

        return ColumnType.TEXT

    def parse_value(self, value, col_type):
        """Parse a value based on its column type"""
        if not value:
            return None

        if col_type == ColumnType.NUMBER:
            try:
                # Remove spaces and convert comma to dot
                return float(value.replace(',', '.').replace(' ', ''))
            except:
                return None
        elif col_type == ColumnType.DATE:
            date_formats = [
                '%d-%m-%Y', '%d/%m/%Y', '%d.%m.%Y',  # European formats
                '%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d',  # ISO formats
                '%m/%d/%Y', '%m-%d-%Y', '%m.%d.%Y',  # US formats
            ]
            for fmt in date_formats:
                try:
                    return datetime.strptime(value, fmt)
                except:
                    pass
            return None
        else:
            return value

    def load_file(self):
        filename = filedialog.askopenfilename(
            title="Select CSV file",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )

        if filename:
            try:
                self.delimiter = self.delimiter_var.get()

                # Read file and detect structure
                with open(filename, 'r', encoding=self.encoding) as file:
                    # Read a sample to detect column types
                    sample_reader = csv.DictReader(file, delimiter=self.delimiter)
                    sample_data = []
                    for i, row in enumerate(sample_reader):
                        sample_data.append(row)
                        if i >= 100:  # Read up to 100 rows for type detection
                            break

                    if not sample_data:
                        messagebox.showerror("Error", "No data found in file")
                        return

                    # Get column names
                    self.column_names = list(sample_data[0].keys())

                    # Detect column types
                    self.column_types = {}
                    for column in self.column_names:
                        values = [row[column] for row in sample_data]
                        self.column_types[column] = self.detect_column_type(values)

                # Now read the full file with proper parsing
                self.data = []
                with open(filename, 'r', encoding=self.encoding) as file:
                    reader = csv.DictReader(file, delimiter=self.delimiter)
                    for row in reader:
                        parsed_row = {}
                        for column in self.column_names:
                            parsed_row[column] = self.parse_value(
                                row[column],
                                self.column_types[column]
                            )
                        self.data.append(parsed_row)

                self.file_label.config(text=os.path.basename(filename))
                self.filtered_data = self.data.copy()
                self.update_column_display()
                self.update_preview()
                self.setup_dynamic_trees()

                # Clear filters and rules when loading new file
                self.global_filters = []
                self.sampling_rules = []
                self.update_filters_display()
                self.update_rules_display()

                messagebox.showinfo("Success",
                                    f"Loaded {len(self.data)} records with {len(self.column_names)} columns")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to load file: {str(e)}")

    def update_column_display(self):
        """Update the column list display"""
        self.column_listbox.delete(0, tk.END)

        for column in self.column_names:
            col_type = self.column_types[column]
            self.column_listbox.insert(tk.END, f"{column} [{col_type}]")

    def update_preview(self):
        """Update the data preview"""
        # Clear existing columns
        for col in self.preview_tree['columns']:
            self.preview_tree.heading(col, text='')
        self.preview_tree['columns'] = ()

        # Set new columns
        self.preview_tree['columns'] = self.column_names

        # Configure columns
        for col in self.column_names:
            self.preview_tree.heading(col, text=col)
            self.preview_tree.column(col, width=120)

        # Clear existing data
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)

        # Add preview data (first 50 rows)
        for row in self.data[:50]:
            values = []
            for col in self.column_names:
                value = row[col]
                if value is None:
                    values.append('')
                elif self.column_types[col] == ColumnType.NUMBER:
                    values.append(f"{value:,.2f}".replace(',', ' ').replace('.', ','))
                elif self.column_types[col] == ColumnType.DATE:
                    values.append(value.strftime('%d-%m-%Y'))
                else:
                    values.append(str(value))
            self.preview_tree.insert('', tk.END, values=values)

        # Update info
        self.info_label.config(text=f"Showing {min(50, len(self.data))} of {len(self.data)} records")

    def setup_dynamic_trees(self):
        """Setup the results tree with dynamic columns"""
        # Results tree columns: Rule Name + all data columns
        columns = ['Rule'] + self.column_names
        self.results_tree['columns'] = columns

        for col in columns:
            self.results_tree.heading(col, text=col)
            self.results_tree.column(col, width=120)

    def add_global_filter(self):
        if not self.column_names:
            messagebox.showwarning("Warning", "Please load a CSV file first")
            return

        # Get available columns (not already filtered)
        filtered_columns = [f.column for f in self.global_filters]
        available_columns = [col for col in self.column_names if col not in filtered_columns]

        if not available_columns:
            messagebox.showwarning("Warning", "All columns already have global filters")
            return

        dialog = GlobalFilterDialog(self.root, "Add Global Filter",
                                    available_columns, self.column_types, self.data)
        self.root.wait_window(dialog.dialog)

        if dialog.result:
            self.global_filters.append(dialog.result)
            self.update_filters_display()
            self.apply_global_filters()

    def edit_global_filter(self):
        selection = self.filters_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a filter to edit")
            return

        index = self.filters_tree.index(selection[0])
        filter_obj = self.global_filters[index]

        # For editing, include the current column in available columns
        filtered_columns = [f.column for f in self.global_filters if f != filter_obj]
        available_columns = [col for col in self.column_names if col not in filtered_columns]

        dialog = GlobalFilterDialog(self.root, "Edit Global Filter",
                                    available_columns, self.column_types, self.data, filter_obj)
        self.root.wait_window(dialog.dialog)

        if dialog.result:
            self.global_filters[index] = dialog.result
            self.update_filters_display()
            self.apply_global_filters()

    def delete_global_filter(self):
        selection = self.filters_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a filter to delete")
            return

        index = self.filters_tree.index(selection[0])
        del self.global_filters[index]
        self.update_filters_display()
        self.apply_global_filters()

    def clear_global_filters(self):
        if self.global_filters and messagebox.askyesno("Confirm", "Clear all global filters?"):
            self.global_filters = []
            self.update_filters_display()
            self.apply_global_filters()

    def update_filters_display(self):
        """Update the filters tree display"""
        for item in self.filters_tree.get_children():
            self.filters_tree.delete(item)

        for filter_obj in self.global_filters:
            self.filters_tree.insert('', tk.END, values=(
                filter_obj.column,
                filter_obj.column_type,
                filter_obj.get_description()
            ))

    def apply_global_filters(self):
        """Apply all global filters to the data"""
        if not self.data:
            return

        # Start with all data
        self.filtered_data = self.data.copy()

        # Apply each filter in sequence (AND logic)
        for filter_obj in self.global_filters:
            self.filtered_data = filter_obj.apply_filter(self.filtered_data)

        # Update count
        self.global_filtered_label.config(
            text=f"Total records: {len(self.data)} | After global filters: {len(self.filtered_data)}"
        )

        # Update sampling rules to show available counts
        self.update_rules_display()

    def add_sampling_rule(self):
        if not self.column_names:
            messagebox.showwarning("Warning", "Please load a CSV file first")
            return

        dialog = SamplingRuleDialog(self.root, "Add Sampling Rule",
                                    self.column_names, self.column_types, self.data)
        self.root.wait_window(dialog.dialog)

        if dialog.result:
            self.sampling_rules.append(dialog.result)
            self.update_rules_display()

    def edit_sampling_rule(self):
        selection = self.rules_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a rule to edit")
            return

        index = self.rules_tree.index(selection[0])
        rule = self.sampling_rules[index]

        dialog = SamplingRuleDialog(self.root, "Edit Sampling Rule",
                                    self.column_names, self.column_types, self.data, rule)
        self.root.wait_window(dialog.dialog)

        if dialog.result:
            self.sampling_rules[index] = dialog.result
            self.update_rules_display()

    def delete_sampling_rule(self):
        selection = self.rules_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a rule to delete")
            return

        index = self.rules_tree.index(selection[0])
        del self.sampling_rules[index]
        self.update_rules_display()

    def clear_sampling_rules(self):
        if self.sampling_rules and messagebox.askyesno("Confirm", "Clear all sampling rules?"):
            self.sampling_rules = []
            self.update_rules_display()

    def update_rules_display(self):
        """Update the rules tree display"""
        for item in self.rules_tree.get_children():
            self.rules_tree.delete(item)

        total_required = 0

        for rule in self.sampling_rules:
            # Count available records for this rule
            available = 0
            for row in self.filtered_data:
                if rule.matches(row):
                    available += 1

            self.rules_tree.insert('', tk.END, values=(
                rule.name,
                rule.get_description(),
                rule.sample_count,
                available
            ))

            total_required += rule.sample_count

        self.total_required_label.config(text=f"Total samples required: {total_required}")

    def generate_stratified_sample(self):
        """Generate stratified sample based on rules"""
        if not self.filtered_data:
            messagebox.showwarning("Warning", "No data available. Apply global filters first.")
            return

        if not self.sampling_rules:
            messagebox.showwarning("Warning", "No sampling rules defined.")
            return

        # Clear previous results
        self.results = []

        # Track which records have been sampled to avoid duplicates
        sampled_indices = set()

        # Process each sampling rule
        rule_results = []
        for i, rule in enumerate(self.sampling_rules):
            self.progress_label.config(text=f"Processing rule {i+1} of {len(self.sampling_rules)}...")
            self.root.update()

            # Find matching records that haven't been sampled yet
            matching_records = []
            for idx, row in enumerate(self.filtered_data):
                if idx not in sampled_indices and rule.matches(row):
                    matching_records.append((idx, row))

            # Sample from matching records
            sample_size = min(rule.sample_count, len(matching_records))

            if sample_size < rule.sample_count:
                messagebox.showwarning("Warning",
                                       f"Rule '{rule.name}' requested {rule.sample_count} samples but only {len(matching_records)} available")

            if sample_size > 0:
                sampled = random.sample(matching_records, sample_size)

                for idx, row in sampled:
                    sampled_indices.add(idx)
                    result = row.copy()
                    result['_rule_name'] = rule.name
                    self.results.append(result)

                rule_results.append(f"{rule.name}: {sample_size} samples")
            else:
                rule_results.append(f"{rule.name}: 0 samples (no matches)")

        self.progress_label.config(text="")

        # Update results display
        self.update_results_display()

        # Switch to results tab
        self.notebook.select(self.results_tab)

        # Show summary
        summary = "\n".join(rule_results)
        messagebox.showinfo("Sampling Complete",
                            f"Generated {len(self.results)} total samples:\n\n{summary}")

    def update_results_display(self):
        """Update the results display"""
        # Update summary
        global_filter_summary = " AND ".join(f.get_description() for f in self.global_filters) if self.global_filters else "None"
        self.results_summary_label.config(
            text=f"Total samples: {len(self.results)} | Global filters: {global_filter_summary}"
        )

        # Clear tree
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)

        # Add results
        for result in self.results:
            values = [result['_rule_name']]

            for col in self.column_names:
                value = result.get(col)
                if value is None:
                    values.append('')
                elif self.column_types[col] == ColumnType.NUMBER:
                    values.append(f"{value:,.2f}".replace(',', ' ').replace('.', ','))
                elif self.column_types[col] == ColumnType.DATE:
                    values.append(value.strftime('%d-%m-%Y'))
                else:
                    values.append(str(value))

            self.results_tree.insert('', tk.END, values=values)

    def save_configuration(self):
        """Save filters and rules to a JSON file"""
        if not self.global_filters and not self.sampling_rules:
            messagebox.showwarning("Warning", "No configuration to save")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )

        if filename:
            try:
                # Prepare global filters data
                global_filters_data = []
                for filter_obj in self.global_filters:
                    filter_dict = filter_obj.to_dict()
                    # Convert datetime objects to strings
                    if filter_obj.column_type == ColumnType.DATE:
                        if filter_dict['filter_config'].get('from'):
                            filter_dict['filter_config']['from'] = filter_dict['filter_config']['from'].strftime('%Y-%m-%d')
                        if filter_dict['filter_config'].get('to'):
                            filter_dict['filter_config']['to'] = filter_dict['filter_config']['to'].strftime('%Y-%m-%d')
                    global_filters_data.append(filter_dict)

                # Prepare sampling rules data
                rules_data = []
                for rule in self.sampling_rules:
                    rule_dict = rule.to_dict()
                    # Convert datetime objects to strings
                    if rule.column_type == ColumnType.DATE:
                        if rule_dict['filter_config'].get('from'):
                            rule_dict['filter_config']['from'] = rule_dict['filter_config']['from'].strftime('%Y-%m-%d')
                        if rule_dict['filter_config'].get('to'):
                            rule_dict['filter_config']['to'] = rule_dict['filter_config']['to'].strftime('%Y-%m-%d')
                    rules_data.append(rule_dict)

                save_data = {
                    'column_types': self.column_types,
                    'global_filters': global_filters_data,
                    'sampling_rules': rules_data
                }

                with open(filename, 'w') as f:
                    json.dump(save_data, f, indent=2)

                messagebox.showinfo("Success", f"Configuration saved to {os.path.basename(filename)}")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to save configuration: {str(e)}")

    def load_configuration(self):
        """Load filters and rules from a JSON file"""
        filename = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )

        if filename:
            try:
                with open(filename, 'r') as f:
                    save_data = json.load(f)

                saved_column_types = save_data.get('column_types', {})

                # Verify columns match
                if self.column_types and saved_column_types:
                    missing_columns = set(saved_column_types.keys()) - set(self.column_types.keys())
                    if missing_columns:
                        messagebox.showwarning("Warning",
                                               f"Some columns in saved configuration are not in current data: {missing_columns}")

                # Load global filters
                self.global_filters = []
                for filter_dict in save_data.get('global_filters', []):
                    if filter_dict['column'] in self.column_types:
                        filter_obj = DimensionalFilter()
                        # Convert date strings back to datetime objects
                        if filter_dict['column_type'] == ColumnType.DATE:
                            if filter_dict['filter_config'].get('from'):
                                filter_dict['filter_config']['from'] = datetime.strptime(
                                    filter_dict['filter_config']['from'], '%Y-%m-%d')
                            if filter_dict['filter_config'].get('to'):
                                filter_dict['filter_config']['to'] = datetime.strptime(
                                    filter_dict['filter_config']['to'], '%Y-%m-%d')
                        filter_obj.from_dict(filter_dict)
                        self.global_filters.append(filter_obj)

                # Load sampling rules
                self.sampling_rules = []
                for rule_dict in save_data.get('sampling_rules', []):
                    if rule_dict['column'] in self.column_types:
                        rule = SamplingRule()
                        # Convert date strings back to datetime objects
                        if rule_dict['column_type'] == ColumnType.DATE:
                            if rule_dict['filter_config'].get('from'):
                                rule_dict['filter_config']['from'] = datetime.strptime(
                                    rule_dict['filter_config']['from'], '%Y-%m-%d')
                            if rule_dict['filter_config'].get('to'):
                                rule_dict['filter_config']['to'] = datetime.strptime(
                                    rule_dict['filter_config']['to'], '%Y-%m-%d')
                        rule.from_dict(rule_dict)
                        self.sampling_rules.append(rule)

                self.update_filters_display()
                self.update_rules_display()
                self.apply_global_filters()

                messagebox.showinfo("Success",
                                    f"Loaded {len(self.global_filters)} global filters and {len(self.sampling_rules)} sampling rules")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to load configuration: {str(e)}")

    def export_results(self):
        """Export all sample results to CSV"""
        if not self.results:
            messagebox.showwarning("Warning", "No results to export")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )

        if filename:
            try:
                with open(filename, 'w', newline='', encoding='utf-8') as file:
                    # Write with rule column first, then data columns
                    fieldnames = ['rule'] + self.column_names
                    writer = csv.DictWriter(file, fieldnames=fieldnames, delimiter=self.delimiter)

                    writer.writeheader()

                    for result in self.results:
                        row = {'rule': result['_rule_name']}
                        for col in self.column_names:
                            value = result.get(col)
                            if value is None:
                                row[col] = ''
                            elif self.column_types[col] == ColumnType.NUMBER:
                                row[col] = str(value).replace('.', ',')
                            elif self.column_types[col] == ColumnType.DATE:
                                row[col] = value.strftime('%d-%m-%Y')
                            else:
                                row[col] = str(value)
                        writer.writerow(row)

                messagebox.showinfo("Success", f"Results exported to {os.path.basename(filename)}")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to export: {str(e)}")

    def export_by_rule(self):
        """Export results grouped by rule to separate files"""
        if not self.results:
            messagebox.showwarning("Warning", "No results to export")
            return

        # Ask for directory
        directory = filedialog.askdirectory(title="Select directory for export")
        if not directory:
            return

        try:
            # Group results by rule
            results_by_rule = defaultdict(list)
            for result in self.results:
                results_by_rule[result['_rule_name']].append(result)

            # Export each rule's results
            for rule_name, rule_results in results_by_rule.items():
                # Create safe filename
                safe_name = "".join(c for c in rule_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
                filename = os.path.join(directory, f"{safe_name}.csv")

                with open(filename, 'w', newline='', encoding='utf-8') as file:
                    writer = csv.DictWriter(file, fieldnames=self.column_names, delimiter=self.delimiter)

                    writer.writeheader()

                    for result in rule_results:
                        row = {}
                        for col in self.column_names:
                            value = result.get(col)
                            if value is None:
                                row[col] = ''
                            elif self.column_types[col] == ColumnType.NUMBER:
                                row[col] = str(value).replace('.', ',')
                            elif self.column_types[col] == ColumnType.DATE:
                                row[col] = value.strftime('%d-%m-%Y')
                            else:
                                row[col] = str(value)
                        writer.writerow(row)

            messagebox.showinfo("Success",
                                f"Exported {len(results_by_rule)} files to {directory}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to export: {str(e)}")

    def clear_results(self):
        """Clear all results"""
        if self.results and messagebox.askyesno("Confirm", "Clear all results?"):
            self.results = []
            self.update_results_display()
            self.results_summary_label.config(text="Results cleared")


class GlobalFilterDialog:
    """Dialog for creating/editing a global dimensional filter"""
    def __init__(self, parent, title, available_columns, column_types, data, filter_obj=None):
        self.result = None
        self.available_columns = available_columns
        self.column_types = column_types
        self.data = data
        self.tooltip = None

        # Create dialog
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("500x500")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # Initialize with existing filter or create new
        if filter_obj:
            self.filter_obj = filter_obj
            # If editing, ensure the current column is in available columns
            if filter_obj.column not in available_columns:
                available_columns.append(filter_obj.column)
        else:
            self.filter_obj = DimensionalFilter()

        self.create_widgets()

        # Center the dialog
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")

    def create_widgets(self):
        # Column selection
        col_frame = ttk.Frame(self.dialog, padding="10")
        col_frame.grid(row=0, column=0, sticky=(tk.W, tk.E))

        ttk.Label(col_frame, text="Select Column:").grid(row=0, column=0, sticky=tk.W)
        self.column_var = tk.StringVar(value=self.filter_obj.column if self.filter_obj.column else self.available_columns[0])
        self.column_combo = ttk.Combobox(col_frame, textvariable=self.column_var,
                                         values=self.available_columns, state='readonly', width=30)
        self.column_combo.grid(row=0, column=1, padx=5)
        self.column_combo.bind('<<ComboboxSelected>>', self.on_column_changed)

        # Filter frame (will be populated based on column type)
        self.filter_frame = ttk.LabelFrame(self.dialog, text="Filter Criteria", padding="10")
        self.filter_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        # Initialize filter controls
        self.on_column_changed(None)

        # Buttons
        button_frame = ttk.Frame(self.dialog)
        button_frame.grid(row=2, column=0, pady=10)

        ttk.Button(button_frame, text="OK", command=self.ok_clicked).grid(row=0, column=0, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.cancel_clicked).grid(row=0, column=1, padx=5)

        # Configure grid
        self.dialog.columnconfigure(0, weight=1)
        self.dialog.rowconfigure(1, weight=1)

    def on_column_changed(self, event):
        """Update filter controls when column changes"""
        # Clear existing controls
        for widget in self.filter_frame.winfo_children():
            widget.destroy()

        column = self.column_var.get()
        if not column:
            return

        col_type = self.column_types[column]
        self.filter_frame.config(text=f"Filter Criteria for {column} [{col_type}]")

        if col_type == ColumnType.TEXT:
            self.create_text_filter()
        elif col_type == ColumnType.NUMBER:
            self.create_number_filter()
        elif col_type == ColumnType.DATE:
            self.create_date_filter()

    def create_text_filter(self):
        """Create text filter controls"""
        column = self.column_var.get()

        # Filter type
        self.text_type_var = tk.StringVar(value='equals')
        ttk.Radiobutton(self.filter_frame, text="Equals (select from list)",
                        variable=self.text_type_var, value='equals').grid(row=0, column=0, sticky=tk.W)
        ttk.Radiobutton(self.filter_frame, text="Contains",
                        variable=self.text_type_var, value='contains').grid(row=1, column=0, sticky=tk.W)

        # Get unique values
        unique_values = sorted(set(str(row.get(column, '')) for row in self.data
                                   if row.get(column) is not None))[:100]  # Limit to 100

        # Equals: Checkboxes in scrollable frame
        equals_frame = ttk.LabelFrame(self.filter_frame, text=f"Select values ({len(unique_values)} unique)", padding="5")
        equals_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=5)

        # Select all/none buttons
        button_frame = ttk.Frame(equals_frame)
        button_frame.grid(row=0, column=0, sticky=(tk.W, tk.E))

        self.check_vars = {}

        def select_all():
            for var in self.check_vars.values():
                var.set(True)

        def select_none():
            for var in self.check_vars.values():
                var.set(False)

        ttk.Button(button_frame, text="All", command=select_all, width=6).grid(row=0, column=0, padx=2)
        ttk.Button(button_frame, text="None", command=select_none, width=6).grid(row=0, column=1, padx=2)

        # Create canvas for scrolling
        canvas = tk.Canvas(equals_frame, height=150)
        scrollbar = ttk.Scrollbar(equals_frame, orient="vertical", command=canvas.yview)
        checkbox_frame = ttk.Frame(canvas)

        checkbox_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=checkbox_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.grid(row=1, column=0, sticky=(tk.W, tk.E))
        scrollbar.grid(row=1, column=1, sticky=(tk.N, tk.S))

        # Create checkboxes
        for i, value in enumerate(unique_values):
            var = tk.BooleanVar()
            self.check_vars[value] = var
            display_text = value if len(value) <= 40 else value[:37] + "..."
            cb = ttk.Checkbutton(checkbox_frame, text=display_text, variable=var)
            cb.grid(row=i, column=0, sticky=tk.W, padx=5)

        # Contains: Entry field
        ttk.Label(self.filter_frame, text="Contains text:").grid(row=3, column=0, sticky=tk.W, pady=(10, 0))
        self.contains_entry = ttk.Entry(self.filter_frame, width=40)
        self.contains_entry.grid(row=4, column=0, sticky=(tk.W, tk.E), pady=5)

        # Load existing values if editing
        if self.filter_obj.column == column and self.filter_obj.filter_config:
            filter_type = self.filter_obj.filter_config.get('type', 'equals')
            self.text_type_var.set(filter_type)

            if filter_type == 'equals' and 'values' in self.filter_obj.filter_config:
                for value in self.filter_obj.filter_config['values']:
                    if value in self.check_vars:
                        self.check_vars[value].set(True)
            elif filter_type == 'contains' and 'pattern' in self.filter_obj.filter_config:
                self.contains_entry.insert(0, self.filter_obj.filter_config['pattern'])

    def create_number_filter(self):
        """Create number filter controls"""
        ttk.Label(self.filter_frame, text="Minimum value:").grid(row=0, column=0, sticky=tk.W)
        self.min_entry = ttk.Entry(self.filter_frame, width=20)
        self.min_entry.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(self.filter_frame, text="Maximum value:").grid(row=1, column=0, sticky=tk.W)
        self.max_entry = ttk.Entry(self.filter_frame, width=20)
        self.max_entry.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(self.filter_frame, text="Leave empty for no limit",
                  font=('TkDefaultFont', 9, 'italic')).grid(row=2, column=0, columnspan=2, pady=5)

        # Load existing values if editing
        column = self.column_var.get()
        if self.filter_obj.column == column and self.filter_obj.filter_config:
            if self.filter_obj.filter_config.get('min') is not None:
                self.min_entry.insert(0, str(self.filter_obj.filter_config['min']))
            if self.filter_obj.filter_config.get('max') is not None:
                self.max_entry.insert(0, str(self.filter_obj.filter_config['max']))

    def create_date_filter(self):
        """Create date filter controls"""
        ttk.Label(self.filter_frame, text="From date (DD-MM-YYYY):").grid(row=0, column=0, sticky=tk.W)
        self.from_entry = ttk.Entry(self.filter_frame, width=20)
        self.from_entry.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(self.filter_frame, text="To date (DD-MM-YYYY):").grid(row=1, column=0, sticky=tk.W)
        self.to_entry = ttk.Entry(self.filter_frame, width=20)
        self.to_entry.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(self.filter_frame, text="Leave empty for no limit",
                  font=('TkDefaultFont', 9, 'italic')).grid(row=2, column=0, columnspan=2, pady=5)

        # Load existing values if editing
        column = self.column_var.get()
        if self.filter_obj.column == column and self.filter_obj.filter_config:
            if self.filter_obj.filter_config.get('from'):
                self.from_entry.insert(0, self.filter_obj.filter_config['from'].strftime('%d-%m-%Y'))
            if self.filter_obj.filter_config.get('to'):
                self.to_entry.insert(0, self.filter_obj.filter_config['to'].strftime('%d-%m-%Y'))

    def ok_clicked(self):
        """Validate and save the filter"""
        column = self.column_var.get()
        col_type = self.column_types[column]

        filter_obj = DimensionalFilter(column, col_type)

        if col_type == ColumnType.TEXT:
            filter_type = self.text_type_var.get()
            filter_obj.filter_config['type'] = filter_type

            if filter_type == 'equals':
                # Get selected values
                selected_values = [value for value, var in self.check_vars.items() if var.get()]
                if selected_values:
                    filter_obj.filter_config['values'] = selected_values
                else:
                    messagebox.showwarning("Warning", "No values selected. Filter will match nothing.")
                    return
            else:  # contains
                pattern = self.contains_entry.get().strip()
                if pattern:
                    filter_obj.filter_config['pattern'] = pattern
                else:
                    messagebox.showerror("Error", "Please enter a search pattern")
                    return

        elif col_type == ColumnType.NUMBER:
            try:
                min_val = self.min_entry.get().strip()
                max_val = self.max_entry.get().strip()

                if not min_val and not max_val:
                    messagebox.showerror("Error", "Please enter at least one value")
                    return

                if min_val:
                    filter_obj.filter_config['min'] = float(min_val.replace(',', '.'))
                if max_val:
                    filter_obj.filter_config['max'] = float(max_val.replace(',', '.'))

                if min_val and max_val and filter_obj.filter_config['min'] > filter_obj.filter_config['max']:
                    messagebox.showerror("Error", "Minimum value must be less than maximum value")
                    return
            except ValueError:
                messagebox.showerror("Error", "Invalid number format")
                return

        elif col_type == ColumnType.DATE:
            try:
                from_date = self.from_entry.get().strip()
                to_date = self.to_entry.get().strip()

                if not from_date and not to_date:
                    messagebox.showerror("Error", "Please enter at least one date")
                    return

                if from_date:
                    filter_obj.filter_config['from'] = datetime.strptime(from_date, '%d-%m-%Y')
                if to_date:
                    filter_obj.filter_config['to'] = datetime.strptime(to_date, '%d-%m-%Y')

                if from_date and to_date and filter_obj.filter_config['from'] > filter_obj.filter_config['to']:
                    messagebox.showerror("Error", "From date must be before to date")
                    return
            except ValueError:
                messagebox.showerror("Error", "Invalid date format. Use DD-MM-YYYY")
                return

        self.result = filter_obj
        self.dialog.destroy()

    def cancel_clicked(self):
        self.dialog.destroy()


class SamplingRuleDialog:
    """Dialog for creating/editing a sampling rule with quota"""
    def __init__(self, parent, title, column_names, column_types, data, rule=None):
        self.result = None
        self.column_names = column_names
        self.column_types = column_types
        self.data = data

        # Create dialog
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("500x550")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # Initialize with existing rule or create new
        if rule:
            self.rule = rule
        else:
            self.rule = SamplingRule()

        self.create_widgets()

        # Center the dialog
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")

    def create_widgets(self):
        # Rule name
        name_frame = ttk.Frame(self.dialog, padding="10")
        name_frame.grid(row=0, column=0, sticky=(tk.W, tk.E))

        ttk.Label(name_frame, text="Rule Name:").grid(row=0, column=0, sticky=tk.W)
        self.name_entry = ttk.Entry(name_frame, width=30)
        self.name_entry.grid(row=0, column=1, padx=5)
        self.name_entry.insert(0, self.rule.name)

        # Sample count
        ttk.Label(name_frame, text="Number of Samples:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.sample_spinbox = ttk.Spinbox(name_frame, from_=1, to=10000, width=10)
        self.sample_spinbox.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        self.sample_spinbox.set(self.rule.sample_count)

        # Column selection
        col_frame = ttk.Frame(self.dialog, padding="10")
        col_frame.grid(row=1, column=0, sticky=(tk.W, tk.E))

        ttk.Label(col_frame, text="Select Column:").grid(row=0, column=0, sticky=tk.W)
        self.column_var = tk.StringVar(value=self.rule.column if self.rule.column else self.column_names[0])
        self.column_combo = ttk.Combobox(col_frame, textvariable=self.column_var,
                                         values=self.column_names, state='readonly', width=30)
        self.column_combo.grid(row=0, column=1, padx=5)
        self.column_combo.bind('<<ComboboxSelected>>', self.on_column_changed)

        # Filter frame (will be populated based on column type)
        self.filter_frame = ttk.LabelFrame(self.dialog, text="Sampling Criteria", padding="10")
        self.filter_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        # Initialize filter controls
        self.on_column_changed(None)

        # Buttons
        button_frame = ttk.Frame(self.dialog)
        button_frame.grid(row=3, column=0, pady=10)

        ttk.Button(button_frame, text="OK", command=self.ok_clicked).grid(row=0, column=0, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.cancel_clicked).grid(row=0, column=1, padx=5)

        # Configure grid
        self.dialog.columnconfigure(0, weight=1)
        self.dialog.rowconfigure(2, weight=1)

    def on_column_changed(self, event):
        """Update filter controls when column changes"""
        # Clear existing controls
        for widget in self.filter_frame.winfo_children():
            widget.destroy()

        column = self.column_var.get()
        if not column:
            return

        col_type = self.column_types[column]
        self.filter_frame.config(text=f"Sampling Criteria for {column} [{col_type}]")

        if col_type == ColumnType.TEXT:
            self.create_text_filter()
        elif col_type == ColumnType.NUMBER:
            self.create_number_filter()
        elif col_type == ColumnType.DATE:
            self.create_date_filter()

    def create_text_filter(self):
        """Create text filter controls - simplified for single values"""
        column = self.column_var.get()

        # Get unique values
        unique_values = sorted(set(str(row.get(column, '')) for row in self.data
                                   if row.get(column) is not None))[:100]  # Limit to 100

        # Single selection for sampling rules
        ttk.Label(self.filter_frame, text="Select value(s) to sample:").grid(row=0, column=0, sticky=tk.W)

        # Checkboxes in scrollable frame
        checkbox_frame_container = ttk.Frame(self.filter_frame)
        checkbox_frame_container.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=5)

        # Create canvas for scrolling
        canvas = tk.Canvas(checkbox_frame_container, height=200)
        scrollbar = ttk.Scrollbar(checkbox_frame_container, orient="vertical", command=canvas.yview)
        checkbox_frame = ttk.Frame(canvas)

        checkbox_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=checkbox_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.grid(row=0, column=0, sticky=(tk.W, tk.E))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # Create checkboxes
        self.check_vars = {}
        for i, value in enumerate(unique_values):
            var = tk.BooleanVar()
            self.check_vars[value] = var
            display_text = value if len(value) <= 40 else value[:37] + "..."
            cb = ttk.Checkbutton(checkbox_frame, text=display_text, variable=var)
            cb.grid(row=i, column=0, sticky=tk.W, padx=5)

        # Load existing values if editing
        if self.rule.column == column and self.rule.filter_config:
            if 'values' in self.rule.filter_config:
                for value in self.rule.filter_config['values']:
                    if value in self.check_vars:
                        self.check_vars[value].set(True)

    def create_number_filter(self):
        """Create number filter controls"""
        ttk.Label(self.filter_frame, text="Sample from range:").grid(row=0, column=0, sticky=tk.W, pady=5)

        ttk.Label(self.filter_frame, text="Minimum value:").grid(row=1, column=0, sticky=tk.W)
        self.min_entry = ttk.Entry(self.filter_frame, width=20)
        self.min_entry.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(self.filter_frame, text="Maximum value:").grid(row=2, column=0, sticky=tk.W)
        self.max_entry = ttk.Entry(self.filter_frame, width=20)
        self.max_entry.grid(row=2, column=1, padx=5, pady=5)

        ttk.Label(self.filter_frame, text="Leave empty for no limit",
                  font=('TkDefaultFont', 9, 'italic')).grid(row=3, column=0, columnspan=2, pady=5)

        # Load existing values if editing
        column = self.column_var.get()
        if self.rule.column == column and self.rule.filter_config:
            if self.rule.filter_config.get('min') is not None:
                self.min_entry.insert(0, str(self.rule.filter_config['min']))
            if self.rule.filter_config.get('max') is not None:
                self.max_entry.insert(0, str(self.rule.filter_config['max']))

    def create_date_filter(self):
        """Create date filter controls"""
        ttk.Label(self.filter_frame, text="Sample from date range:").grid(row=0, column=0, sticky=tk.W, pady=5)

        ttk.Label(self.filter_frame, text="From date (DD-MM-YYYY):").grid(row=1, column=0, sticky=tk.W)
        self.from_entry = ttk.Entry(self.filter_frame, width=20)
        self.from_entry.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(self.filter_frame, text="To date (DD-MM-YYYY):").grid(row=2, column=0, sticky=tk.W)
        self.to_entry = ttk.Entry(self.filter_frame, width=20)
        self.to_entry.grid(row=2, column=1, padx=5, pady=5)

        ttk.Label(self.filter_frame, text="Leave empty for no limit",
                  font=('TkDefaultFont', 9, 'italic')).grid(row=3, column=0, columnspan=2, pady=5)

        # Load existing values if editing
        column = self.column_var.get()
        if self.rule.column == column and self.rule.filter_config:
            if self.rule.filter_config.get('from'):
                self.from_entry.insert(0, self.rule.filter_config['from'].strftime('%d-%m-%Y'))
            if self.rule.filter_config.get('to'):
                self.to_entry.insert(0, self.rule.filter_config['to'].strftime('%d-%m-%Y'))

    def ok_clicked(self):
        """Validate and save the rule"""
        # Validate name
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showerror("Error", "Please enter a rule name")
            return

        # Validate sample count
        try:
            sample_count = int(self.sample_spinbox.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid sample count")
            return

        column = self.column_var.get()
        col_type = self.column_types[column]

        rule = SamplingRule(name, column, col_type)
        rule.sample_count = sample_count

        # Set filter config based on type
        if col_type == ColumnType.TEXT:
            # Get selected values
            selected_values = [value for value, var in self.check_vars.items() if var.get()]
            if selected_values:
                rule.filter_config['type'] = 'equals'
                rule.filter_config['values'] = selected_values
            else:
                messagebox.showerror("Error", "Please select at least one value")
                return

        elif col_type == ColumnType.NUMBER:
            try:
                min_val = self.min_entry.get().strip()
                max_val = self.max_entry.get().strip()

                if not min_val and not max_val:
                    messagebox.showerror("Error", "Please enter at least one value")
                    return

                if min_val:
                    rule.filter_config['min'] = float(min_val.replace(',', '.'))
                if max_val:
                    rule.filter_config['max'] = float(max_val.replace(',', '.'))

                if min_val and max_val and rule.filter_config['min'] > rule.filter_config['max']:
                    messagebox.showerror("Error", "Minimum value must be less than maximum value")
                    return
            except ValueError:
                messagebox.showerror("Error", "Invalid number format")
                return

        elif col_type == ColumnType.DATE:
            try:
                from_date = self.from_entry.get().strip()
                to_date = self.to_entry.get().strip()

                if not from_date and not to_date:
                    messagebox.showerror("Error", "Please enter at least one date")
                    return

                if from_date:
                    rule.filter_config['from'] = datetime.strptime(from_date, '%d-%m-%Y')
                if to_date:
                    rule.filter_config['to'] = datetime.strptime(to_date, '%d-%m-%Y')

                if from_date and to_date and rule.filter_config['from'] > rule.filter_config['to']:
                    messagebox.showerror("Error", "From date must be before to date")
                    return
            except ValueError:
                messagebox.showerror("Error", "Invalid date format. Use DD-MM-YYYY")
                return

        self.result = rule
        self.dialog.destroy()

    def cancel_clicked(self):
        self.dialog.destroy()


def main():
    root = tk.Tk()
    app = HybridSampleTestingApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
