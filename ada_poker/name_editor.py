"""Desktop dialog for exact, session-scoped username corrections.

Reads the already-generated summary.json/observations.json for one analysis
output folder, lists every distinct (seat, raw on-screen spelling) pairing
with how many frames showed it and any existing saved correction, and lets
the user type a corrected spelling for exactly that pairing. Nothing here
auto-corrects, fuzzy-merges, or sends data anywhere; every save is an exact,
explicit mapping the user typed for one seat in one recording.
"""
from __future__ import annotations

import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .corrections import load_name_corrections, save_name_correction


class NameEditor(tk.Toplevel):
    def __init__(self, parent, output: Path, on_saved):
        super().__init__(parent)
        self.title("Correct player names")
        self.geometry("780x520")
        self.minsize(650,420)
        self.transient(parent)
        self.grab_set()
        self._output = Path(output).resolve()
        self._on_saved = on_saved
        self._rows = []  # list of dict: seat, raw_name, sightings, corrected_name
        self._selected_index = None

        try:
            self._source_hash, self._rows = self._load_rows()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("Correct player names", f"Could not load session data:\n{exc}",
                                  parent=self)
            self._source_hash = None
            self._rows = []

        self._build_ui()
        self._populate_rows()

    # -- data loading -----------------------------------------------------

    def _load_rows(self):
        summary_path = self._output / "summary.json"
        observations_path = self._output / "observations.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        source_hash = summary.get("source_hash")
        if not source_hash:
            raise ValueError("summary.json is missing source_hash.")
        if observations_path.exists():
            observations = json.loads(observations_path.read_text(encoding="utf-8"))
        else:
            observations = []
        corrections = load_name_corrections(self._output, source_hash)

        counts = {}
        representative = {}
        for observation in observations:
            for seat in observation.get("seats") or []:
                raw_name = seat.get("raw_username") or seat.get("crop_username")
                if raw_name is None:
                    continue
                key = (seat.get("seat"), raw_name)
                counts[key] = counts.get(key, 0) + 1
                if key not in representative:
                    evidence = observation.get("evidence") or {}
                    representative[key] = evidence.get("image")

        rows = []
        for (seat, raw_name), sightings in sorted(counts.items()):
            rows.append({
                "seat": seat,
                "raw_name": raw_name,
                "sightings": sightings,
                "corrected_name": corrections.get((seat, raw_name), ""),
                "image": representative.get((seat, raw_name)),
            })
        return source_hash, rows

    # -- UI -----------------------------------------------------------------

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        list_frame = ttk.Frame(self)
        list_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        columns = ("seat", "raw_name", "sightings", "corrected_name")
        self._tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse")
        self._tree.heading("seat", text="Seat")
        self._tree.heading("raw_name", text="On-screen spelling")
        self._tree.heading("sightings", text="Sightings")
        self._tree.heading("corrected_name", text="Saved correction")
        self._tree.column("seat", width=60, anchor="center")
        self._tree.column("raw_name", width=200)
        self._tree.column("sightings", width=80, anchor="center")
        self._tree.column("corrected_name", width=200)
        self._tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self._tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=scrollbar.set)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)

        editor_frame = ttk.Frame(self)
        editor_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        editor_frame.columnconfigure(1, weight=1)

        ttk.Label(editor_frame, text="Corrected spelling:").grid(row=0, column=0, sticky="w")
        self._name_var = tk.StringVar()
        self._name_entry = ttk.Entry(editor_frame, textvariable=self._name_var)
        self._name_entry.grid(row=0, column=1, sticky="ew", padx=(4, 4))

        self._view_button = ttk.Button(editor_frame, text="View source image",
                                        command=self._view_image)
        self._view_button.grid(row=0, column=2, padx=(4, 0))

        ttk.Label(editor_frame,
                  text="A saved correction applies only to this seat in this recording.",
                  wraplength=520, justify="left").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        button_frame = ttk.Frame(self)
        button_frame.grid(row=2, column=0, sticky="e", padx=8, pady=(0, 8))
        self._save_button = ttk.Button(button_frame, text="Save & refresh", command=self._save)
        self._save_button.grid(row=0, column=0, padx=(0, 4))
        ttk.Button(button_frame, text="Close", command=self.destroy).grid(row=0, column=1)

        self._set_editor_enabled(False)

    def _populate_rows(self):
        for index, row in enumerate(self._rows):
            self._tree.insert("", "end", iid=str(index), values=(
                row["seat"] + 1, row["raw_name"], row["sightings"], row["corrected_name"]))

    def _set_editor_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self._name_entry.configure(state=state)
        self._view_button.configure(state=state)
        self._save_button.configure(state=state)

    def _on_select(self, _event=None):
        selection = self._tree.selection()
        if not selection:
            self._selected_index = None
            self._set_editor_enabled(False)
            return
        self._selected_index = int(selection[0])
        row = self._rows[self._selected_index]
        self._name_var.set(row["corrected_name"] or row["raw_name"])
        self._set_editor_enabled(True)

    def _view_image(self):
        if self._selected_index is None:
            return
        row = self._rows[self._selected_index]
        relative = row.get("image")
        if not relative:
            messagebox.showinfo("Correct player names", "No source image is available for this row.",
                                 parent=self)
            return
        frames_dir = (self._output / "frames").resolve()
        candidate = (self._output / relative).resolve()
        if frames_dir != candidate and frames_dir not in candidate.parents:
            messagebox.showerror("Correct player names",
                                  "Refusing to open a path outside this session's frames.",
                                  parent=self)
            return
        if not candidate.is_file():
            messagebox.showerror("Correct player names", f"Source image not found:\n{candidate}",
                                  parent=self)
            return
        try:
            os.startfile(str(candidate))  # noqa: S606 - local, path-validated above
        except OSError as exc:
            messagebox.showerror("Correct player names", f"Could not open source image:\n{exc}",
                                  parent=self)

    def _save(self):
        if self._selected_index is None or self._source_hash is None:
            return
        row = self._rows[self._selected_index]
        corrected_name = self._name_var.get()
        try:
            save_name_correction(self._output, self._source_hash, row["seat"],
                                  row["raw_name"], corrected_name)
        except (ValueError,OSError) as exc:
            messagebox.showerror("Correct player names", f"Could not save correction:\n{exc}",
                                  parent=self)
            return

        on_saved = self._on_saved
        self.destroy()
        if on_saved is not None:
            on_saved()
