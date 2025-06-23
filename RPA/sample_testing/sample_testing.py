import os
import csv
import random
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
import json
from collections import OrderedDict


class ColumnType:
    """Enum for column data types"""
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    UNKNOWN = "unknown"


class GenericSamplingRule:
    """Generic sampling rule that can handle any CSV structure"""
    def __init__(self, name=""):
        self.name = name
        self.filters = {}  # {column_name: filter_config}
        self.sample_count = 5

    def to_dict(self):
        return {
            'name': self.name,
            'filters': self.filters,
            'sample_count': self.sample_count
        }

    def from_dict(self, data):
        self.name = data.get('name', '')
        self.filters = data.get('filters', {})
        self.sample_count = data.get('sample_count', 5)
        return self

    def apply_filter(self, data, column_types):
        """Apply filters to data based on column types"""
        filtered = data.copy()

        for column, filter_config in self.filters.items():
            if column not in column_types:
                continue

            col_type = column_types[column]
            filter_type = filter_config.get('type')

            if col_type == ColumnType.TEXT:
                if filter_type == 'equals':
                    values = filter_config.get('values', [])
                    if values:
                        filtered = [row for row in filtered if row.get(column) in values]
                elif filter_type == 'contains':
                    pattern = filter_config.get('pattern', '')
                    if pattern:
                        filtered = [row for row in filtered
                                    if pattern.lower() in str(row.get(column, '')).lower()]

            elif col_type == ColumnType.NUMBER:
                min_val = filter_config.get('min')
                max_val = filter_config.get('max')
                if min_val is not None:
                    filtered = [row for row in filtered
                                if row.get(column) is not None and row[column] >= min_val]
                if max_val is not None:
                    filtered = [row for row in filtered
                                if row.get(column) is not None and row[column] <= max_val]

            elif col_type == ColumnType.DATE:
                date_from = filter_config.get('from')
                date_to = filter_config.get('to')
                if date_from:
                    filtered = [row for row in filtered
                                if row.get(column) and row[column] >= date_from]
                if date_to:
                    filtered = [row for row in filtered
                                if row.get(column) and row[column] <= date_to]

        return filtered


class GenericSampleTestingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Generic CSV Sample Testing Tool")
        self.root.geometry("1200x800")

        # Data storage
        self.data = []
        self.column_names = []
        self.column_types = {}
        self.rules = []
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

        # Tab 2: Sampling Rules
        self.rules_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.rules_tab, text="Sampling Rules")
        self.create_rules_tab()

        # Tab 3: Results
        self.results_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.results_tab, text="Results")
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

        # Create preview tree (will be populated dynamically)
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

    def create_rules_tab(self):
        # Top frame with buttons
        button_frame = ttk.Frame(self.rules_tab, padding="10")
        button_frame.grid(row=0, column=0, sticky=(tk.W, tk.E))

        ttk.Button(button_frame, text="Add Rule", command=self.add_rule).grid(row=0, column=0, padx=5)
        ttk.Button(button_frame, text="Edit Rule", command=self.edit_rule).grid(row=0, column=1, padx=5)
        ttk.Button(button_frame, text="Delete Rule", command=self.delete_rule).grid(row=0, column=2, padx=5)
        ttk.Button(button_frame, text="Save Rules", command=self.save_rules).grid(row=0, column=3, padx=5)
        ttk.Button(button_frame, text="Load Rules", command=self.load_rules).grid(row=0, column=4, padx=5)

        # Rules list
        rules_frame = ttk.LabelFrame(self.rules_tab, text="Sampling Rules", padding="10")
        rules_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        columns = ('Rule Name', 'Filters', 'Sample Count')
        self.rules_tree = ttk.Treeview(rules_frame, columns=columns, show='headings', height=15)

        for col in columns:
            self.rules_tree.heading(col, text=col)

        self.rules_tree.column('Rule Name', width=200)
        self.rules_tree.column('Filters', width=500)
        self.rules_tree.column('Sample Count', width=100)

        rules_scroll = ttk.Scrollbar(rules_frame, orient=tk.VERTICAL, command=self.rules_tree.yview)
        self.rules_tree.configure(yscrollcommand=rules_scroll.set)

        self.rules_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        rules_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # Generate samples button
        generate_frame = ttk.Frame(self.rules_tab, padding="10")
        generate_frame.grid(row=2, column=0, sticky=(tk.W, tk.E))

        ttk.Button(generate_frame, text="Generate All Samples",
                   command=self.generate_all_samples,
                   style='Accent.TButton').grid(row=0, column=0)

        self.progress_label = ttk.Label(generate_frame, text="")
        self.progress_label.grid(row=0, column=1, padx=20)

        # Configure grid
        self.rules_tab.columnconfigure(0, weight=1)
        self.rules_tab.rowconfigure(1, weight=1)
        rules_frame.columnconfigure(0, weight=1)
        rules_frame.rowconfigure(0, weight=1)

    def create_results_tab(self):
        # Results summary
        summary_frame = ttk.Frame(self.results_tab, padding="10")
        summary_frame.grid(row=0, column=0, sticky=(tk.W, tk.E))

        self.results_summary_label = ttk.Label(summary_frame, text="No results generated yet")
        self.results_summary_label.grid(row=0, column=0)

        # Results tree (will be created dynamically based on columns)
        results_frame = ttk.LabelFrame(self.results_tab, text="Sample Results", padding="10")
        results_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        self.results_tree = ttk.Treeview(results_frame, show='headings', height=15)
        results_scroll = ttk.Scrollbar(results_frame, orient=tk.VERTICAL, command=self.results_tree.yview)
        self.results_tree.configure(yscrollcommand=results_scroll.set)

        self.results_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        results_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # Export button
        export_frame = ttk.Frame(self.results_tab, padding="10")
        export_frame.grid(row=2, column=0, sticky=(tk.W, tk.E))

        ttk.Button(export_frame, text="Export Results to CSV", command=self.export_results).grid(row=0, column=0)

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
        date_formats = ['%d-%m-%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%Y/%m/%d']
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
                float(value.replace(',', '.'))
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
                return float(value.replace(',', '.'))
            except:
                return None
        elif col_type == ColumnType.DATE:
            date_formats = ['%d-%m-%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%Y/%m/%d']
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
                self.update_column_display()
                self.update_preview()
                self.setup_dynamic_trees()

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

    def add_rule(self):
        if not self.column_names:
            messagebox.showwarning("Warning", "Please load a CSV file first")
            return

        dialog = GenericRuleDialog(self.root, "Add Sampling Rule",
                                   self.column_names, self.column_types, self.data)
        if dialog.result:
            self.rules.append(dialog.result)
            self.update_rules_display()

    def edit_rule(self):
        selection = self.rules_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a rule to edit")
            return

        index = self.rules_tree.index(selection[0])
        rule = self.rules[index]

        dialog = GenericRuleDialog(self.root, "Edit Sampling Rule",
                                   self.column_names, self.column_types, self.data, rule)
        if dialog.result:
            self.rules[index] = dialog.result
            self.update_rules_display()

    def delete_rule(self):
        selection = self.rules_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a rule to delete")
            return

        if messagebox.askyesno("Confirm", "Delete selected rule?"):
            index = self.rules_tree.index(selection[0])
            del self.rules[index]
            self.update_rules_display()

    def update_rules_display(self):
        """Update the rules tree display"""
        for item in self.rules_tree.get_children():
            self.rules_tree.delete(item)

        for rule in self.rules:
            # Create a summary of filters
            filter_summary = []
            for column, config in rule.filters.items():
                if self.column_types[column] == ColumnType.TEXT:
                    if config['type'] == 'equals':
                        values = config.get('values', [])
                        if values:
                            filter_summary.append(f"{column} in [{', '.join(values[:3])}...]")
                    elif config['type'] == 'contains':
                        pattern = config.get('pattern', '')
                        if pattern:
                            filter_summary.append(f"{column} contains '{pattern}'")
                elif self.column_types[column] == ColumnType.NUMBER:
                    parts = []
                    if config.get('min') is not None:
                        parts.append(f">= {config['min']}")
                    if config.get('max') is not None:
                        parts.append(f"<= {config['max']}")
                    if parts:
                        filter_summary.append(f"{column} {' and '.join(parts)}")
                elif self.column_types[column] == ColumnType.DATE:
                    parts = []
                    if config.get('from'):
                        parts.append(f"from {config['from'].strftime('%d-%m-%Y')}")
                    if config.get('to'):
                        parts.append(f"to {config['to'].strftime('%d-%m-%Y')}")
                    if parts:
                        filter_summary.append(f"{column} {' '.join(parts)}")

            self.rules_tree.insert('', tk.END, values=(
                rule.name,
                '; '.join(filter_summary) if filter_summary else 'No filters',
                rule.sample_count
            ))

    def generate_all_samples(self):
        if not self.data:
            messagebox.showwarning("Warning", "Please load data first")
            return

        if not self.rules:
            messagebox.showwarning("Warning", "Please create at least one sampling rule")
            return

        self.results = []
        total_samples = 0

        for i, rule in enumerate(self.rules):
            self.progress_label.config(text=f"Processing rule {i+1} of {len(self.rules)}...")
            self.root.update()

            # Apply filters
            filtered_data = rule.apply_filter(self.data, self.column_types)

            if not filtered_data:
                messagebox.showwarning("Warning", f"No data matches rule '{rule.name}'")
                continue

            # Generate samples
            sample_size = min(rule.sample_count, len(filtered_data))
            samples = random.sample(filtered_data, sample_size)

            # Store results with rule name
            for sample in samples:
                result = sample.copy()
                result['_rule_name'] = rule.name
                self.results.append(result)

            total_samples += len(samples)

        self.progress_label.config(text="")
        self.update_results_display()

        # Switch to results tab
        self.notebook.select(self.results_tab)

        messagebox.showinfo("Success",
                            f"Generated {total_samples} total samples across {len(self.rules)} rules")

    def update_results_display(self):
        """Update the results display"""
        # Update summary
        total_samples = len(self.results)
        unique_rules = len(set(r['_rule_name'] for r in self.results))
        self.results_summary_label.config(
            text=f"Total samples: {total_samples} | Rules applied: {unique_rules}"
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

    def save_rules(self):
        """Save rules to a JSON file"""
        if not self.rules:
            messagebox.showwarning("Warning", "No rules to save")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )

        if filename:
            try:
                rules_data = []
                for rule in self.rules:
                    rule_dict = rule.to_dict()
                    # Convert datetime objects to strings for JSON serialization
                    for column, config in rule_dict['filters'].items():
                        if self.column_types.get(column) == ColumnType.DATE:
                            if config.get('from'):
                                config['from'] = config['from'].strftime('%Y-%m-%d')
                            if config.get('to'):
                                config['to'] = config['to'].strftime('%Y-%m-%d')
                    rules_data.append(rule_dict)

                save_data = {
                    'column_types': self.column_types,
                    'rules': rules_data
                }

                with open(filename, 'w') as f:
                    json.dump(save_data, f, indent=2)

                messagebox.showinfo("Success", f"Rules saved to {os.path.basename(filename)}")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to save rules: {str(e)}")

    def load_rules(self):
        """Load rules from a JSON file"""
        filename = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )

        if filename:
            try:
                with open(filename, 'r') as f:
                    save_data = json.load(f)

                rules_data = save_data.get('rules', [])
                saved_column_types = save_data.get('column_types', {})

                # Verify columns match
                if self.column_types and saved_column_types:
                    missing_columns = set(saved_column_types.keys()) - set(self.column_types.keys())
                    if missing_columns:
                        messagebox.showwarning("Warning",
                                               f"Some columns in saved rules are not in current data: {missing_columns}")

                self.rules = []
                for rule_dict in rules_data:
                    rule = GenericSamplingRule()
                    # Convert date strings back to datetime objects
                    for column, config in rule_dict['filters'].items():
                        if saved_column_types.get(column) == ColumnType.DATE:
                            if config.get('from'):
                                config['from'] = datetime.strptime(config['from'], '%Y-%m-%d')
                            if config.get('to'):
                                config['to'] = datetime.strptime(config['to'], '%Y-%m-%d')
                    rule.from_dict(rule_dict)
                    self.rules.append(rule)

                self.update_rules_display()
                messagebox.showinfo("Success", f"Loaded {len(self.rules)} rules")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to load rules: {str(e)}")

    def export_results(self):
        """Export results to CSV"""
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


class GenericRuleDialog:
    """Dialog for creating/editing sampling rules with dynamic filters"""
    def __init__(self, parent, title, column_names, column_types, data, rule=None):
        self.result = None
        self.column_names = column_names
        self.column_types = column_types
        self.data = data
        self.filter_widgets = {}

        # Create dialog
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("600x600")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # Initialize with existing rule or create new
        if rule:
            self.rule = rule
        else:
            self.rule = GenericSamplingRule()

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
        self.name_entry = ttk.Entry(name_frame, width=40)
        self.name_entry.grid(row=0, column=1, padx=5)
        self.name_entry.insert(0, self.rule.name)

        # Sample count
        ttk.Label(name_frame, text="Sample Count:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.sample_spinbox = ttk.Spinbox(name_frame, from_=1, to=10000, width=10)
        self.sample_spinbox.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        self.sample_spinbox.set(self.rule.sample_count)

        # Filters frame with scrollbar
        filters_label = ttk.Label(self.dialog, text="Filters (leave empty for no filter)",
                                  font=('TkDefaultFont', 9, 'italic'))
        filters_label.grid(row=1, column=0, sticky=tk.W, padx=10)

        # Create canvas and scrollbar for filters
        canvas_frame = ttk.Frame(self.dialog)
        canvas_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        canvas = tk.Canvas(canvas_frame, height=400)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        self.scrollable_frame = ttk.Frame(canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # Create filter controls for each column
        for i, column in enumerate(self.column_names):
            self.create_filter_control(self.scrollable_frame, column, i)

        # Buttons
        button_frame = ttk.Frame(self.dialog)
        button_frame.grid(row=3, column=0, pady=10)

        ttk.Button(button_frame, text="OK", command=self.ok_clicked).grid(row=0, column=0, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.cancel_clicked).grid(row=0, column=1, padx=5)

        # Configure grid
        self.dialog.columnconfigure(0, weight=1)
        self.dialog.rowconfigure(2, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        canvas_frame.rowconfigure(0, weight=1)

    def create_filter_control(self, parent, column, row):
        """Create appropriate filter control based on column type"""
        col_type = self.column_types[column]

        # Column label
        frame = ttk.LabelFrame(parent, text=f"{column} [{col_type}]", padding="5")
        frame.grid(row=row, column=0, sticky=(tk.W, tk.E), padx=5, pady=2)

        widgets = {}

        if col_type == ColumnType.TEXT:
            # Text filter options
            filter_type_var = tk.StringVar(value='equals')
            ttk.Radiobutton(frame, text="Equals", variable=filter_type_var,
                            value='equals').grid(row=0, column=0)
            ttk.Radiobutton(frame, text="Contains", variable=filter_type_var,
                            value='contains').grid(row=0, column=1)

            # Get unique values for dropdown
            unique_values = sorted(set(str(row.get(column, '')) for row in self.data
                                       if row.get(column) is not None))[:100]  # Limit to 100

            # Equals: Multi-select listbox
            equals_frame = ttk.Frame(frame)
            equals_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E))

            listbox = tk.Listbox(equals_frame, selectmode=tk.MULTIPLE, height=4)
            listbox_scroll = ttk.Scrollbar(equals_frame, orient=tk.VERTICAL, command=listbox.yview)
            listbox.configure(yscrollcommand=listbox_scroll.set)

            for value in unique_values:
                listbox.insert(tk.END, value)

            listbox.grid(row=0, column=0, sticky=(tk.W, tk.E))
            listbox_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))

            # Contains: Entry field
            ttk.Label(frame, text="Contains text:").grid(row=2, column=0, sticky=tk.W)
            contains_entry = ttk.Entry(frame, width=30)
            contains_entry.grid(row=2, column=1, sticky=(tk.W, tk.E))

            widgets = {
                'type_var': filter_type_var,
                'listbox': listbox,
                'contains_entry': contains_entry,
                'unique_values': unique_values
            }

        elif col_type == ColumnType.NUMBER:
            # Number range filter
            ttk.Label(frame, text="Min:").grid(row=0, column=0, sticky=tk.W)
            min_entry = ttk.Entry(frame, width=15)
            min_entry.grid(row=0, column=1, padx=5)

            ttk.Label(frame, text="Max:").grid(row=1, column=0, sticky=tk.W)
            max_entry = ttk.Entry(frame, width=15)
            max_entry.grid(row=1, column=1, padx=5)

            widgets = {
                'min_entry': min_entry,
                'max_entry': max_entry
            }

        elif col_type == ColumnType.DATE:
            # Date range filter
            ttk.Label(frame, text="From (DD-MM-YYYY):").grid(row=0, column=0, sticky=tk.W)
            from_entry = ttk.Entry(frame, width=15)
            from_entry.grid(row=0, column=1, padx=5)

            ttk.Label(frame, text="To (DD-MM-YYYY):").grid(row=1, column=0, sticky=tk.W)
            to_entry = ttk.Entry(frame, width=15)
            to_entry.grid(row=1, column=1, padx=5)

            widgets = {
                'from_entry': from_entry,
                'to_entry': to_entry
            }

        # Load existing filter values
        if column in self.rule.filters:
            filter_config = self.rule.filters[column]

            if col_type == ColumnType.TEXT:
                widgets['type_var'].set(filter_config.get('type', 'equals'))

                if filter_config.get('type') == 'equals' and 'values' in filter_config:
                    # Select items in listbox
                    for i, value in enumerate(widgets['unique_values']):
                        if value in filter_config['values']:
                            widgets['listbox'].selection_set(i)
                elif filter_config.get('type') == 'contains' and 'pattern' in filter_config:
                    widgets['contains_entry'].insert(0, filter_config['pattern'])

            elif col_type == ColumnType.NUMBER:
                if filter_config.get('min') is not None:
                    widgets['min_entry'].insert(0, str(filter_config['min']))
                if filter_config.get('max') is not None:
                    widgets['max_entry'].insert(0, str(filter_config['max']))

            elif col_type == ColumnType.DATE:
                if filter_config.get('from'):
                    widgets['from_entry'].insert(0, filter_config['from'].strftime('%d-%m-%Y'))
                if filter_config.get('to'):
                    widgets['to_entry'].insert(0, filter_config['to'].strftime('%d-%m-%Y'))

        self.filter_widgets[column] = widgets

    def ok_clicked(self):
        """Validate and save the rule"""
        rule = GenericSamplingRule()

        # Name
        rule.name = self.name_entry.get().strip()
        if not rule.name:
            messagebox.showerror("Error", "Please enter a rule name")
            return

        # Sample count
        try:
            rule.sample_count = int(self.sample_spinbox.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid sample count")
            return

        # Collect filters
        for column, widgets in self.filter_widgets.items():
            col_type = self.column_types[column]
            filter_config = {}
            has_filter = False

            if col_type == ColumnType.TEXT:
                filter_type = widgets['type_var'].get()
                filter_config['type'] = filter_type

                if filter_type == 'equals':
                    selected_indices = widgets['listbox'].curselection()
                    if selected_indices:
                        selected_values = [widgets['unique_values'][i] for i in selected_indices]
                        filter_config['values'] = selected_values
                        has_filter = True
                else:  # contains
                    pattern = widgets['contains_entry'].get().strip()
                    if pattern:
                        filter_config['pattern'] = pattern
                        has_filter = True

            elif col_type == ColumnType.NUMBER:
                try:
                    min_val = widgets['min_entry'].get().strip()
                    if min_val:
                        filter_config['min'] = float(min_val.replace(',', '.'))
                        has_filter = True

                    max_val = widgets['max_entry'].get().strip()
                    if max_val:
                        filter_config['max'] = float(max_val.replace(',', '.'))
                        has_filter = True

                    if 'min' in filter_config and 'max' in filter_config:
                        if filter_config['min'] > filter_config['max']:
                            messagebox.showerror("Error",
                                                 f"Min value must be less than max value for {column}")
                            return
                except ValueError:
                    messagebox.showerror("Error", f"Invalid number format for {column}")
                    return

            elif col_type == ColumnType.DATE:
                try:
                    from_date = widgets['from_entry'].get().strip()
                    if from_date:
                        filter_config['from'] = datetime.strptime(from_date, '%d-%m-%Y')
                        has_filter = True

                    to_date = widgets['to_entry'].get().strip()
                    if to_date:
                        filter_config['to'] = datetime.strptime(to_date, '%d-%m-%Y')
                        has_filter = True

                    if 'from' in filter_config and 'to' in filter_config:
                        if filter_config['from'] > filter_config['to']:
                            messagebox.showerror("Error",
                                                 f"From date must be before to date for {column}")
                            return
                except ValueError:
                    messagebox.showerror("Error",
                                         f"Invalid date format for {column}. Use DD-MM-YYYY")
                    return

            if has_filter:
                rule.filters[column] = filter_config

        self.result = rule
        self.dialog.destroy()

    def cancel_clicked(self):
        self.dialog.destroy()


def main():
    root = tk.Tk()
    app = GenericSampleTestingApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
