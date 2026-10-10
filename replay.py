"""Run the 20 real September phone transcripts through the post-call pipeline and score the verdicts.

  .venv/bin/python replay.py            # needs GEMINI_API_KEY (or MOCK_LLM=1 for a plumbing test)
Real HubSpot and email are never touched here; everything goes to outbox/.
"""
import re, sys
from datetime import datetime
from pathlib import Path
from app import agent, db, config

# Expected verdicts from services.md + qualified.md. Judgement calls are flagged.
EXPECTED = {
 "T01": "qualified", "T02": "qualified", "T03": "declined", "T04": "declined", "T05": "qualified", "T06": "qualified",
 "T07": "not_now", "T08": "missed", "T09": "escalate", "T10": "declined", "T11": "qualified", "T12": "qualified",
 "T13": "qualified", "T14": "qualified", "T15": "qualified", "T16": "escalate", "T17": "qualified", "T18": "declined",
 "T19": "declined", "T20": "qualified",
}
NOTES = {"T18": "180 sq ft pod: services.md caps commercial at 3,000 sq ft but sets no minimum. The 500 sq ft minimum was only said by the front desk.",
         "T16": "Repeat caller after a missed follow-up. Re-logged as an enquiry, but flagged for senior handling. Judgement call.",
         "T07": "Timeline too short now, caller may return after Diwali."}

def parse():
    t = Path("data/enquiries_sep2026.txt").read_text()
    sec = t.split("SECTION 2")[0]
    blocks = re.split(r"\n\n(?=T\d\d ·)", sec)[1:]
    out = []
    for b in blocks:
        head, *body = b.strip().split("\n")
        tid = head[:3]
        m = re.search(r"· (\d+) (\w+) · (\d+):(\d+)(am|pm)", head)
        day, mon, hh, mm, ap = int(m.group(1)), m.group(2), int(m.group(3)), int(m.group(4)), m.group(5)
        hh = hh % 12 + (12 if ap == "pm" else 0)
        when = datetime.strptime(f"{day} {mon} 2026 {hh}:{mm}", "%d %B %Y %H:%M")
        d = re.search(r"(\d+) min (\d+) sec", head)
        dur = int(d.group(1)) * 60 + int(d.group(2)) if d else 120
        turns = []
        for line in body:
            mm_ = re.match(r"(Front Desk|Caller): (.*)", line)
            if mm_:
                turns.append({"who": "agent" if mm_.group(1) == "Front Desk" else "caller", "text": mm_.group(2)})
        missed = "MISSED CALL" in head
        out.append({"id": tid, "when": when, "dur": dur, "turns": turns, "missed": missed})
    return out

def main():
    only = sys.argv[1:]
    rows = []
    for c in parse():
        if only and c["id"] not in only:
            continue
        # the original front desk lines are replaced by the AI in production; here the AI is scored on what it concludes from the call
        rec = agent.finalize(c["turns"], c["when"], c["dur"], "replay", c["id"], call_id=c["id"], live_integrations=False, missed=c["missed"])
        exp = EXPECTED[c["id"]]
        rows.append((c["id"], exp, rec["verdict"], rec["gist"]))
        print(f"{c['id']}  expected {exp:10s} got {rec['verdict']:10s} {'OK ' if exp == rec['verdict'] else 'DIFF'}  {rec['gist'][:90]}")
    ok = sum(1 for r in rows if r[1] == r[2])
    print(f"\n{ok}/{len(rows)} match my reading of the rubric. See NOTES in replay.py for the judgement calls.")

if __name__ == "__main__":
    main()
