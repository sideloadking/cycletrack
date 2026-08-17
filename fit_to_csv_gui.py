import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import csv

# You need: pip install fitparse

def convert_fit_to_csv(fit_path, output_folder, mode, status_callback):
    try:
        from fitparse import FitFile
    except ImportError:
        messagebox.showerror("Missing dependency", "Please install fitparse first:\n\npip install fitparse")
        return False

    try:
        fitfile = FitFile(fit_path)
        status_callback("Reading .FIT file...")

        all_rows = []
        all_field_names = set()

        for msg in fitfile.get_messages():
            msg_type = msg.name
            if mode == "records_only" and msg_type != "record":
                continue

            row = {"message_type": msg_type}
            has_data = False
            for field in msg:
                val = field.value
                if val is not None and not isinstance(val, (str, int, float, bool)):
                    val = str(val)
                row[field.name] = val
                all_field_names.add(field.name)
                has_data = True
            
            if has_data:
                all_rows.append(row)

        if not all_rows:
            messagebox.showwarning("No data", "No data found. Try All Data mode." if mode == "records_only" else "No readable data found.")
            return False

        os.makedirs(output_folder, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(fit_path))[0]

        other_fields = sorted(all_field_names)
        fieldnames = ["message_type"]
        if "timestamp" in other_fields:
            fieldnames.append("timestamp")
            other_fields.remove("timestamp")
        fieldnames.extend(other_fields)

        suffix = "record" if mode == "records_only" else "complete"
        out_path = os.path.join(output_folder, f"{base_name}_{suffix}.csv")

        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_rows)

        status_callback(f"Done! Created {os.path.basename(out_path)}")
        messagebox.showinfo("Success", f"Converted successfully!\n\nCreated 1 file:\n{out_path}\n\nTotal rows: {len(all_rows)}\nTotal columns: {len(fieldnames)}")
        return True

    except Exception as e:
        messagebox.showerror("Error", f"Failed to convert:\n{e}")
        status_callback("Error occurred.")
        return False


class FitToCsvApp:
    def __init__(self, root):
        root.title("FIT to CSV Converter")
        root.geometry("600x420")
        root.minsize(600, 420)
        root.resizable(True, False)

        main_frame = ttk.Frame(root, padding=20)
        main_frame.pack(fill="both", expand=True)

        ttk.Label(main_frame, text="FIT to CSV Converter", font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(0, 5))
        ttk.Label(main_frame, text="Single CSV output - no data lost.", foreground="#666").pack(anchor="w", pady=(0, 15))

        self.fit_path_var = tk.StringVar()
        file_row = ttk.Frame(main_frame)
        file_row.pack(fill="x", pady=5)
        ttk.Label(file_row, text=".FIT File:", width=12).pack(side="left")
        ttk.Entry(file_row, textvariable=self.fit_path_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(file_row, text="Browse", command=self.browse_fit).pack(side="left")

        self.output_path_var = tk.StringVar()
        out_row = ttk.Frame(main_frame)
        out_row.pack(fill="x", pady=5)
        ttk.Label(out_row, text="Output Folder:", width=12).pack(side="left")
        ttk.Entry(out_row, textvariable=self.output_path_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(out_row, text="Browse", command=self.browse_output).pack(side="left")

        self.mode_var = tk.StringVar(value="all")
        mode_frame = ttk.LabelFrame(main_frame, text="Conversion Mode (single file output)", padding=10)
        mode_frame.pack(fill="x", pady=12)
        ttk.Radiobutton(mode_frame, text="All Data - One CSV with message_type column (lossless)", variable=self.mode_var, value="all").pack(anchor="w", pady=3, fill="x")
        ttk.Radiobutton(mode_frame, text="Only Records - One CSV with just GPS/HR/Power/Cadence", variable=self.mode_var, value="records_only").pack(anchor="w", pady=3, fill="x")

        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill="x", side="bottom", pady=(10, 0))

        self.status_var = tk.StringVar(value="Ready. Select a .FIT file.")
        ttk.Label(bottom_frame, textvariable=self.status_var, foreground="#444").pack(anchor="w", pady=(0, 8))
        ttk.Button(bottom_frame, text="Convert to CSV", command=self.convert, style="Accent.TButton").pack(fill="x", ipady=6)

        try:
            style = ttk.Style()
            style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        except:
            pass

    def browse_fit(self):
        path = filedialog.askopenfilename(filetypes=[("FIT Files", "*.fit"), ("All Files", "*.*")])
        if path:
            self.fit_path_var.set(path)
            if not self.output_path_var.get():
                self.output_path_var.set(os.path.dirname(path))
            self.status_var.set(f"Selected: {os.path.basename(path)}")

    def browse_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output_path_var.set(path)

    def convert(self):
        fit_path = self.fit_path_var.get().strip()
        out_path = self.output_path_var.get().strip()
        if not fit_path or not os.path.exists(fit_path):
            messagebox.showwarning("Missing file", "Please select a valid .FIT file.")
            return
        if not out_path:
            messagebox.showwarning("Missing folder", "Please select an output folder.")
            return
        self.status_var.set("Converting...")
        root.update_idletasks()
        convert_fit_to_csv(fit_path, out_path, self.mode_var.get(), lambda msg: self.status_var.set(msg))


if __name__ == "__main__":
    root = tk.Tk()
    app = FitToCsvApp(root)
    root.mainloop()
