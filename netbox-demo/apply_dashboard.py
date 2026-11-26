#!/usr/bin/env python3
"""Apply dashboard.yaml as a user's NetBox home dashboard (default user: admin).

Usage:
    python3 netbox-demo/apply_dashboard.py --dry-run   # validate every widget, change nothing
    python3 netbox-demo/apply_dashboard.py             # replace admin's dashboard

Runs inside the NetBox container through `manage.py shell` (kubectl exec), because NetBox
stores dashboards per user and has no REST endpoint to write them. Each widget's config is
validated with NetBox's own widget ConfigForm before anything is saved; one invalid widget
aborts the whole run. The YAML is the source of truth: re-applying overwrites changes made
in the UI.
"""
import argparse
import json
import pathlib
import subprocess

import yaml

ap = argparse.ArgumentParser()
ap.add_argument("--user", default="admin")
ap.add_argument("--dry-run", action="store_true")
args = ap.parse_args()

widgets = yaml.safe_load((pathlib.Path(__file__).parent / "dashboard.yaml").read_text())["widgets"]
payload = json.dumps({"user": args.user, "dry_run": args.dry_run, "widgets": widgets})

# Runs inside NetBox. Reads the payload from stdin.
CODE = r'''
import json, sys
from django.contrib.auth import get_user_model
from extras.dashboard.utils import get_default_dashboard, get_widget_class
from extras.models import Dashboard

p = json.load(sys.stdin)
errors = []
for w in p["widgets"]:
    form = get_widget_class(w["widget"]).ConfigForm(data=w.get("config", {}))
    if not form.is_valid():
        errors.append(f"{w.get('title')}: {form.errors.as_text()}")
    else:
        print(f"valid   {w['widget']:<28} {w.get('title')}")
if errors:
    print("INVALID:\n" + "\n".join(errors))
    sys.exit(1)

user = get_user_model().objects.get(username=p["user"])
if p["dry_run"]:
    print(f"dry run: {len(p['widgets'])} widgets valid; {p['user']}'s dashboard not changed")
    sys.exit(0)
new = get_default_dashboard(config=p["widgets"])
dash, _ = Dashboard.objects.get_or_create(user=user)
dash.layout, dash.config = new.layout, new.config
dash.save()
print(f"applied {len(p['widgets'])} widgets to {p['user']}'s dashboard")
'''

cmd = ["kubectl", "-n", "netbox", "exec", "-i", "deploy/netbox", "-c", "netbox", "--",
       "/opt/netbox/venv/bin/python", "/opt/netbox/netbox/manage.py", "shell",
       "--no-startup", "--no-imports", "--interface", "python", "-c", CODE]
r = subprocess.run(cmd, input=payload, text=True, capture_output=True)
print(r.stdout.strip())
if r.returncode:
    print(r.stderr.strip()[-2000:])
raise SystemExit(r.returncode)
