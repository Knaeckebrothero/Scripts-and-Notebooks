import os
import csv
import random
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime


class SampleTestingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Financial Audit Sample Testing - Standard Library Version")
        self.root.geometry("900x600")

        # Data storage
        self.data = []
        self.filtered_data = []

        # Create main frames
        self.create_widgets()

    def create_widgets(self):
        # File selection frame
        file_frame = ttk.Frame(self.root, padding="10")
        file_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E))

        ttk.Label(file_frame, text="CSV File:").grid(row=0, column=0, sticky=tk.W)
        self.file_label = ttk.Label(file_frame, text="No file selected", relief=tk.SUNKEN)
        self.file_label.grid(row=0, column=1, padx=5, sticky=(tk.W, tk.E))
        ttk.Button(file_frame, text="Browse", command=self.load_file).grid(row=0, column=2)

        # Filter frame
        filter_frame = ttk.LabelFrame(self.root, text="Filters", padding="10")
        filter_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        ttk.Label(filter_frame, text="Date Before:").grid(row=0, column=0, sticky=tk.W)
        self.date_filter = ttk.Entry(filter_frame, width=15)
        self.date_filter.grid(row=0, column=1, padx=5)
        self.date_filter.insert(0, "31-12-2024")

        ttk.Label(filter_frame, text="Min Value:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.min_value = ttk.Entry(filter_frame, width=15)
        self.min_value.grid(row=1, column=1, padx=5)

        ttk.Label(filter_frame, text="Max Value:").grid(row=2, column=0, sticky=tk.W)
        self.max_value = ttk.Entry(filter_frame, width=15)
        self.max_value.grid(row=2, column=1, padx=5)

        ttk.Button(filter_frame, text="Apply Filters", command=self.apply_filters).grid(row=3, column=0, columnspan=2, pady=10)

        # Sample selection frame
        sample_frame = ttk.LabelFrame(self.root, text="Sample Selection", padding="10")
        sample_frame.grid(row=1, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        ttk.Label(sample_frame, text="Number of Samples:").grid(row=0, column=0, sticky=tk.W)
        self.sample_count = ttk.Spinbox(sample_frame, from_=1, to=100, width=10)
        self.sample_count.set(5)
        self.sample_count.grid(row=0, column=1, padx=5)

        ttk.Label(sample_frame, text="Available Records:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.available_label = ttk.Label(sample_frame, text="0")
        self.available_label.grid(row=1, column=1, sticky=tk.W, padx=5)

        ttk.Button(sample_frame, text="Generate Sample", command=self.generate_sample).grid(row=2, column=0, columnspan=2, pady=10)

        # Results frame
        results_frame = ttk.LabelFrame(self.root, text="Sample Results", padding="10")
        results_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        # Create Treeview for results
        columns = ('ID', 'Key Figure', 'Value', 'Date')
        self.tree = ttk.Treeview(results_frame, columns=columns, show='headings', height=10)

        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=150)

        # Add scrollbar
        scrollbar = ttk.Scrollbar(results_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(2, weight=1)
        results_frame.columnconfigure(0, weight=1)

    def load_file(self):
        filename = filedialog.askopenfilename(
            title="Select CSV file",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )

        if filename:
            try:
                self.data = []
                with open(filename, 'r', encoding='utf-8') as file:
                    reader = csv.DictReader(file, delimiter=';')
                    for row in reader:
                        # Parse the data
                        parsed_row = {
                            'id': row['id'],
                            'key figure': row['key figure'],
                            'value': self.parse_value(row['value']),
                            'date': self.parse_date(row['date'])
                        }
                        self.data.append(parsed_row)

                self.file_label.config(text=os.path.basename(filename))
                self.filtered_data = self.data.copy()
                self.update_available_count()
                messagebox.showinfo("Success", f"Loaded {len(self.data)} records")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to load file: {str(e)}")

    def parse_value(self, value_str):
        """Parse European number format"""
        try:
            # Replace comma with dot for decimal separator
            return float(value_str.replace(',', '.'))
        except:
            return 0.0

    def parse_date(self, date_str):
        """Parse date in DD-MM-YYYY format"""
        try:
            return datetime.strptime(date_str, '%d-%m-%Y')
        except:
            return None

    def apply_filters(self):
        if not self.data:
            messagebox.showwarning("Warning", "Please load a file first")
            return

        self.filtered_data = self.data.copy()

        # Apply date filter
        date_filter_str = self.date_filter.get()
        if date_filter_str:
            try:
                date_limit = datetime.strptime(date_filter_str, '%d-%m-%Y')
                self.filtered_data = [row for row in self.filtered_data
                                      if row['date'] and row['date'] <= date_limit]
            except:
                messagebox.showerror("Error", "Invalid date format. Use DD-MM-YYYY")
                return

        # Apply value filters
        try:
            if self.min_value.get():
                min_val = float(self.min_value.get())
                self.filtered_data = [row for row in self.filtered_data
                                      if row['value'] >= min_val]

            if self.max_value.get():
                max_val = float(self.max_value.get())
                self.filtered_data = [row for row in self.filtered_data
                                      if row['value'] <= max_val]
        except ValueError:
            messagebox.showerror("Error", "Invalid value format")
            return

        self.update_available_count()
        messagebox.showinfo("Success", f"Filters applied. {len(self.filtered_data)} records match criteria")

    def update_available_count(self):
        self.available_label.config(text=str(len(self.filtered_data)))

    def generate_sample(self):
        if not self.filtered_data:
            messagebox.showwarning("Warning", "No data available for sampling")
            return

        try:
            sample_size = int(self.sample_count.get())

            if sample_size > len(self.filtered_data):
                messagebox.showwarning("Warning",
                                       f"Sample size ({sample_size}) is larger than available records ({len(self.filtered_data)}). "
                                       f"Using all available records.")
                sample_size = len(self.filtered_data)

            # Generate random sample
            sample = random.sample(self.filtered_data, sample_size)

            # Clear existing results
            for item in self.tree.get_children():
                self.tree.delete(item)

            # Display results
            for row in sample:
                self.tree.insert('', tk.END, values=(
                    row['id'],
                    row['key figure'],
                    f"{row['value']:,.2f}".replace(',', ' ').replace('.', ','),  # European format
                    row['date'].strftime('%d-%m-%Y') if row['date'] else ''
                ))

        except ValueError:
            messagebox.showerror("Error", "Invalid sample size")

def main():
    root = tk.Tk()
    app = SampleTestingApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
