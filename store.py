"""Load an inbox JSON. Never changes the source file."""

 

import json

from collections import Counter

from pathlib import Path

 

REQUIRED_FIELDS = ("id", "thread_id", "from", "to", "subject", "timestamp", "body", "unread")

 

def domain_of(addr):

    addr = addr or ""

    if "@" in addr:

        return addr.rsplit("@", 1)[-1].lower()

    return addr.lower()

 

def infer_owner(messages):

    """Mailbox owner is usually whoever appears most often in `to`."""

    counts = Counter()

    for m in messages:

        to_addr = (m.get("to") or "").lower()

        if to_addr:

            counts[to_addr] += 1

    if not counts:

        return ""

    return counts.most_common(1)[0][0]

 

class MailStore:

    """Read-only mailbox. inbox.json is never written."""

 

    def __init__(self, messages, owner, path=None):

        self.messages = list(messages)

        self.owner = (owner or "").lower()

        self.path = path

        self._by_id = {}

        self._threads = {}

        for m in self.messages:

            self._by_id[m["id"]] = m

            tid = m["thread_id"]

            if tid not in self._threads:

                self._threads[tid] = []

            self._threads[tid].append(m)

        for items in self._threads.values():

            items.sort(key=lambda x: x.get("timestamp") or "")

 

    @classmethod

    def load(cls, path, owner=""):

        inbox_path = Path(path).resolve()

        data = json.loads(inbox_path.read_text(encoding="utf-8"))

        if not isinstance(data, list):

            raise ValueError(f"{inbox_path} must be a JSON array of messages")

        if data:

            missing = [f for f in REQUIRED_FIELDS if f not in data[0]]

            if missing:

                raise ValueError(f"{inbox_path} messages missing fields: {missing}")

        owner_addr = (owner or infer_owner(data)).lower()

        return cls(data, owner_addr, inbox_path)

 

    def get(self, message_id):

        return self._by_id.get(message_id)

 

    def thread(self, thread_id):

        return list(self._threads.get(thread_id, []))

 

    def thread_for(self, msg):

        return self.thread(msg.get("thread_id") or "")

 

    def earlier_in_thread(self, msg):

        earlier = []

        stamp = msg.get("timestamp") or ""

        for m in self.thread_for(msg):

            if m["id"] == msg["id"]:

                continue

            if (m.get("timestamp") or "") < stamp:

                earlier.append(m)

        return earlier