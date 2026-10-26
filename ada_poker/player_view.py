"""Primary local player search and hand history view."""
from pathlib import Path
import sqlite3
import tkinter as tk
from tkinter import ttk
from urllib.parse import quote
import webbrowser

from .storage import search_players, load_player_hands


def hand_text(hand: dict) -> str:
    """Readable saved observations, with uncertainty attached to the record."""
    def cards(value):
        return ' '.join(value) if value else 'Unknown / not observed'
    def amount(value):
        return 'unknown' if value is None else f'{value} BB'
    lines = [f"Hand: {hand['id']}",
             f"Status: {'Needs checking' if hand.get('needs_review', True) else 'Reviewed'}",
             f"Board: {cards(hand.get('board'))}    Your cards: {cards(hand.get('hero_cards'))}",
             '', 'Players observed at the table (participation may be unknown):']
    for seat in hand.get('seats', []):
        source = {'user': 'corrected', 'mixed': 'partly corrected'}.get(seat.get('username_source'), 'unverified reading')
        lines.append(f"  {seat.get('username') or 'Unknown'} — {seat.get('position') or 'unknown position'}"
                     f" ({seat.get('position_status', 'unknown')}); stack {amount(seat.get('stack_bb'))}; {source}")
        parts=seat.get('hole_card_parts',[])
        if any(c.get('rank') or c.get('suit') for c in parts):
            visible=' '.join((c.get('rank') or '?')+(c.get('suit') or '?') for c in parts)
            lines.append(f'    Visible cards: {visible} (? means unreadable)')
    lines.extend(['', 'Recorded events (sequence may be incomplete):'])
    for event in hand.get('events', []):
        value = ''
        if event.get('action') == 'stack_change':
            value = f"; stack now {amount(event.get('stack_after_bb'))} (action unknown)"
        elif event.get('amount_bb') is not None:
            value = f"; amount {amount(event['amount_bb'])}"
        elif event.get('to_bb') is not None:
            value = f"; displayed total {amount(event['to_bb'])} (increment unknown)"
        lines.append(f"  {event.get('time', 0):.2f}s | {event.get('street', 'unknown')} | "
                     f"{event.get('username') or 'Unknown player'}: {event.get('action', 'unknown').replace('_', ' ')}{value}")
    if not hand.get('events'):
        lines.append('  No actions recovered.')
    issues = list(hand.get('issues', []))
    for seat in hand.get('seats', []):
        issues.extend(f"{seat.get('username') or 'Unknown player'}: {issue}" for issue in seat.get('issues', []))
    for event in hand.get('events', []):
        issues.extend(f"{event.get('time', 0):.2f}s: {issue}" for issue in event.get('issues', []))
    if issues:
        lines.extend(['', 'Details needing checking:'] + ['  ' + issue.replace('_', ' ') for issue in dict.fromkeys(issues)])
    return '\n'.join(lines)


def evidence_url(analysis_root: Path, record: dict) -> str:
    root = Path(analysis_root).resolve()
    folder = (root / record['session_id']).resolve()
    if folder.parent != root:
        raise ValueError('Invalid session evidence location.')
    report = folder / 'review.html'
    if not report.is_file():
        raise FileNotFoundError('The evidence page is missing. Analyze this recording again in Recordings & checks.')
    return report.as_uri() + '#hand=' + quote(record['hand']['id'], safe='')


class PlayerView(ttk.Frame):
    PAGE_SIZE = 50

    def __init__(self, parent, analysis_root):
        super().__init__(parent, padding=16)
        self.analysis_root = Path(analysis_root)
        self.db_path = self.analysis_root / 'players.sqlite3'
        self.query = tk.StringVar()
        self.status = tk.StringVar()
        self.player_offset = self.hand_offset = 0
        self.username = None
        self.player_rows = []
        self.hand_rows = []
        self.selected_record = None
        ttk.Label(self, text='Find a player', font=('Segoe UI', 22)).pack(anchor='w')
        ttk.Label(self, text='All recorded hands together at the table, including folds. Different spellings stay separate.').pack(anchor='w', pady=(4,12))
        bar = ttk.Frame(self)
        bar.pack(fill='x')
        self.entry = ttk.Entry(bar, textvariable=self.query)
        self.entry.pack(side='left', fill='x', expand=True)
        self.entry.bind('<Return>', lambda event: self.search())
        ttk.Button(bar, text='Search', command=self.search).pack(side='left', padx=8)
        panes = ttk.Panedwindow(self, orient='horizontal')
        panes.pack(fill='both', expand=True, pady=12)
        left, right = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=3)
        self.players = self.tree(left, ('Username', 'Hands', 'Recordings'), (190,65,80), height=8)
        self.players.bind('<<TreeviewSelect>>', self.select_player)
        self.player_prev, self.player_next = self.pages(left, self.page_players)
        self.hand_title = ttk.Label(right, text='Select a username to see their history.')
        self.hand_title.pack(anchor='w', pady=(0,6))
        self.hands = self.tree(right, ('Recording', 'Time in video', 'Status'), (245,130,110), height=5)
        self.hands.bind('<<TreeviewSelect>>', self.select_hand)
        self.hand_prev, self.hand_next = self.pages(right, self.page_hands)
        detail_frame = ttk.Frame(right)
        detail_frame.pack(fill='both', expand=True, pady=8)
        self.details = tk.Text(detail_frame, height=10, wrap='word', state='disabled', font=('Segoe UI',10))
        scroll = ttk.Scrollbar(detail_frame, orient='vertical', command=self.details.yview)
        self.details.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.details.pack(fill='both', expand=True)
        self.check_button = ttk.Button(right, text='Check uncertain hand against recording', command=self.open_evidence, state='disabled')
        self.check_button.pack(anchor='w')
        ttk.Label(self, textvariable=self.status, wraplength=950).pack(fill='x')
        self.search()

    @staticmethod
    def tree(parent, columns, widths, height):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=columns, show='headings', height=height, selectmode='browse')
        for column, width in zip(columns, widths):
            tree.heading(column, text=column)
            tree.column(column, width=width, minwidth=55)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        tree.pack(fill='both', expand=True)
        return tree

    @staticmethod
    def pages(parent, command):
        frame = ttk.Frame(parent)
        frame.pack(fill='x', pady=4)
        previous = ttk.Button(frame, text='Previous', command=lambda: command(-1), state='disabled')
        following = ttk.Button(frame, text='Next', command=lambda: command(1), state='disabled')
        previous.pack(side='left')
        following.pack(side='left', padx=6)
        return previous, following

    def clear_hand(self):
        self.selected_record = None
        self.check_button.configure(state='disabled')
        self.details.configure(state='normal')
        self.details.delete('1.0','end')
        self.details.configure(state='disabled')

    def search(self, reset=True):
        if reset:
            self.player_offset = 0
        self.username = None
        self.player_rows = self.hand_rows = []
        self.players.delete(*self.players.get_children())
        self.hands.delete(*self.hands.get_children())
        self.clear_hand()
        self.hand_title.configure(text='Select a username to see their history.')
        for button in (self.player_prev,self.player_next,self.hand_prev,self.hand_next):
            button.configure(state='disabled')
        try:
            rows = search_players(self.db_path, self.query.get(), limit=self.PAGE_SIZE+1, offset=self.player_offset)
            self.player_rows = rows[:self.PAGE_SIZE]
            for i, row in enumerate(self.player_rows):
                self.players.insert('', 'end', iid=str(i), values=(row['username'],row['hand_count'],row['session_count']))
            self.player_prev.configure(state='normal' if self.player_offset else 'disabled')
            self.player_next.configure(state='normal' if len(rows)>self.PAGE_SIZE else 'disabled')
            self.status.set(f'Showing {len(self.player_rows)} usernames. Hand counts include drafts; they are not verified statistics.' if rows else
                            'No matching players. Try part of the name, or add a recording in Recordings & checks.')
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.status.set(f'Could not read player history: {exc}')

    def page_players(self, direction):
        self.player_offset = max(0,self.player_offset + direction*self.PAGE_SIZE)
        self.search(reset=False)

    def select_player(self, event=None):
        selected = self.players.selection()
        if not selected:
            return
        row = self.player_rows[int(selected[0])]
        self.username = row['username']
        self.hand_offset = 0
        self.hand_title.configure(text=f"{self.username} — {row['hand_count']} hands across {row['session_count']} recordings")
        self.show_hands()

    def page_hands(self, direction):
        self.hand_offset = max(0,self.hand_offset + direction*self.PAGE_SIZE)
        self.show_hands()

    def show_hands(self):
        self.hands.delete(*self.hands.get_children())
        self.hand_rows = []
        self.clear_hand()
        for button in (self.hand_prev,self.hand_next):
            button.configure(state='disabled')
        try:
            rows = load_player_hands(self.db_path,self.username,limit=self.PAGE_SIZE+1,offset=self.hand_offset)
            self.hand_rows = rows[:self.PAGE_SIZE]
            for i, row in enumerate(self.hand_rows):
                h = row['hand']
                self.hands.insert('', 'end', iid=str(i), values=(row['session_id'],
                    f"{h['start_time']:.1f}–{h['end_time']:.1f}s", 'Needs checking' if h.get('needs_review',True) else 'Reviewed'))
            self.hand_prev.configure(state='normal' if self.hand_offset else 'disabled')
            self.hand_next.configure(state='normal' if len(rows)>self.PAGE_SIZE else 'disabled')
            self.status.set(f'Hands {self.hand_offset+1}–{self.hand_offset+len(self.hand_rows)}. Select a hand to read its saved details.' if rows else 'No hands on this page.')
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.status.set(f'Could not read hands: {exc}')

    def select_hand(self, event=None):
        selected = self.hands.selection()
        if not selected:
            return
        self.selected_record = self.hand_rows[int(selected[0])]
        hand = self.selected_record['hand']
        self.details.configure(state='normal')
        self.details.delete('1.0','end')
        self.details.insert('1.0', hand_text(hand))
        self.details.configure(state='disabled')
        self.check_button.configure(state='normal' if hand.get('needs_review',True) else 'disabled')

    def open_evidence(self):
        if not self.selected_record:
            return
        try:
            webbrowser.open(evidence_url(self.analysis_root, self.selected_record))
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
