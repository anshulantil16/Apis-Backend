"""The real APIS conference rooms — single source of truth for what "a fresh
RoomPulse database" looks like.

Deliberately duplicated (not imported) from migrations/0002_seed_rooms.py:
that migration already ran successfully everywhere, and migrations should
stay frozen once applied rather than reach into live app code that could
change later. This module is for anything that needs the same seed data
going forward (currently: the Super Admin reset endpoint).
"""
SEED_ROOMS = [
    {'name': 'Conference Room - 1', 'label': '(Apis)',     'floor': '1st Floor', 'color': '#f59e0b'},
    {'name': 'Conference Room - 2', 'label': '(Misk)',      'floor': '2nd Floor', 'color': '#6366f1'},
    {'name': 'Conference Room - 3', 'label': '(Nutrasip)',  'floor': '2nd Floor', 'color': '#10b981'},
]
SEED_ROOM_DEFAULTS = {'capacity': 12, 'amenities': ['Projector', 'Whiteboard', 'Video Conferencing']}
