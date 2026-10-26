"""Player history, local recording analysis, and uncertainty checking."""
from pathlib import Path
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .analyze import analyze

ANALYSIS_ROOT = Path.home()/"Videos"/"Ada Poker Analysis"


class ReviewApp:
    def __init__(self, root):
        self.root = root
        root.title("Ada Poker · Offline review")
        root.geometry("1120x760")
        root.minsize(1000,650)
        self.queue = queue.Queue()
        self.busy = False
        self.after_id = None
        self.source = tk.StringVar()
        self.status = tk.StringVar(value="Add a recording to your player history, or check uncertain readings.")
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both",expand=True)
        from .player_view import PlayerView
        self.player_view = PlayerView(self.tabs, ANALYSIS_ROOT)
        self.tabs.add(self.player_view,text="Players")
        panel = ttk.Frame(self.tabs,padding=24)
        self.tabs.add(panel,text="Recordings & checks")
        ttk.Label(panel,text="Add recordings and check readings",font=("Segoe UI",22)).pack(anchor="w",pady=(0,10))
        ttk.Label(panel,text="Analysis adds hands to player search. Use the evidence to check doubtful details.").pack(anchor="w",pady=(0,20))
        self.sessions = ttk.Combobox(panel,textvariable=self.source,state="readonly",width=80)
        self.sessions.pack(fill="x",pady=8)
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x",pady=12)
        self.analyze_button = ttk.Button(buttons,text="Analyze recording",command=self.run)
        self.analyze_button.pack(side="left")
        self.open_button = ttk.Button(buttons,text="Check recording",command=self.open)
        self.open_button.pack(side="left",padx=8)
        self.browse_button = ttk.Button(buttons,text="Choose recording folder…",command=self.browse)
        self.browse_button.pack(side="left")
        self.correct_button = ttk.Button(panel,text="Correct player names…",command=self.correct_names)
        self.correct_button.pack(anchor="w",pady=4)
        ttk.Label(panel,textvariable=self.status,wraplength=700).pack(fill="x",pady=18)
        ttk.Label(panel,text="Names and amounts can contain reading errors. Card matches cover the current\ncalibration only. Use Correct player names to save verified spellings.",wraplength=700).pack(anchor="w",pady=10)
        self.refresh()
        root.protocol("WM_DELETE_WINDOW",self.close)
        self.poll()

    def refresh(self):
        folder = Path.home()/"Videos"/"Ada Poker"
        settings = Path(__file__).resolve().parents[1]/".local"/"settings.json"
        try:
            folder = Path(json.loads(settings.read_text(encoding="utf-8"))["output_root"])
        except (OSError,ValueError,KeyError):
            pass
        values = sorted((str(p.parent) for p in folder.glob("*/segments.csv")),reverse=True)
        self.sessions["values"] = values
        if values:
            self.source.set(values[0])

    def browse(self):
        folder = filedialog.askdirectory(parent=self.root,title="Choose a session containing segments.csv")
        if folder:
            self.source.set(folder)

    def output(self):
        return ANALYSIS_ROOT/Path(self.source.get()).name

    def run(self):
        if self.busy or not self.source.get():
            return
        source,output = Path(self.source.get()),self.output()
        self.busy = True
        for widget in (self.analyze_button,self.browse_button,self.open_button,self.correct_button,self.sessions):
            widget.configure(state="disabled")
        self.status.set("Preparing local analysis…")
        def worker():
            try:
                analyze(source,output,lambda value:self.queue.put(("progress",value)))
                self.queue.put(("done",output))
            except Exception as exc:
                self.queue.put(("error",str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def open(self):
        path = self.output()/"review.html"
        if path.is_file():
            os.startfile(str(path))
        else:
            self.status.set("No review yet. Click Analyze recording first.")

    def correct_names(self):
        if self.busy:
            return
        if not (self.output()/"observations.json").is_file():
            self.status.set("Analyze this recording before correcting names.")
            return
        from .name_editor import NameEditor
        NameEditor(self.root,self.output(),self.run)

    def poll(self):
        while not self.queue.empty():
            kind,value = self.queue.get_nowait()
            if kind == "progress":
                self.status.set(value)
            else:
                self.busy = False
                for widget in (self.analyze_button,self.browse_button,self.open_button,self.correct_button):
                    widget.configure(state="normal")
                self.sessions.configure(state="readonly")
                if kind == "done":
                    self.player_view.search()
                self.status.set("Analysis saved. Search for a username in Players." if kind=="done" else f"Analysis failed: {value}")
        self.after_id = self.root.after(200,self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo("Analysis is running","Let this analysis finish before closing the review window.",parent=self.root)
            return
        if self.after_id:
            self.root.after_cancel(self.after_id)
        self.root.destroy()


def maintenance_main():
    from .windows import enable_dpi_awareness
    enable_dpi_awareness()
    root = tk.Tk()
    ReviewApp(root)
    root.mainloop()


def main():
    import sys
    if '--maintenance' in sys.argv:
        sys.argv.remove('--maintenance')
        maintenance_main()
    else:
        from .ui import main as search_main
        search_main()


if __name__ == "__main__":
    main()

