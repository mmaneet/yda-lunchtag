from __future__ import annotations

import argparse
import random
from pathlib import Path

from portal.config import default_db_path
from portal.features.lunchtag.engine.config import DEFAULT_GENERATION, DEFAULT_SCORING
from portal.features.lunchtag.engine.generator import generate_best
from portal.features.lunchtag.engine.scoring import score_groups
from portal.storage.repo import Repository


def _print_groups(groups, player_map):
    for idx, group in enumerate(groups, start=1):
        names = [player_map[pid] for pid in group]
        print(f"Group {idx} ({len(group)}): {', '.join(names)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lunchtag CLI")
    sub = parser.add_subparsers(dest="command")

    players = sub.add_parser("players")
    players_sub = players.add_subparsers(dest="action")
    players_sub.add_parser("list")
    add = players_sub.add_parser("add")
    add.add_argument("name")

    draft = sub.add_parser("draft")
    draft_sub = draft.add_subparsers(dest="action")
    draft_sub.add_parser("show")
    draft_sub.add_parser("confirm")

    sub.add_parser("generate")

    history = sub.add_parser("history")
    history.add_subparsers(dest="action").add_parser("list")

    reset = sub.add_parser("reset")
    reset.add_argument("scope", choices=["history", "full"])

    role = sub.add_parser("role")
    role_sub = role.add_subparsers(dest="action")
    role_sub.add_parser("list")
    role_set = role_sub.add_parser("set")
    role_set.add_argument("email")
    role_set.add_argument("role", choices=["member", "admin"])

    link = sub.add_parser("link")
    link.add_argument("email")
    link.add_argument("player_id", type=int)

    notif = sub.add_parser("notifications")
    notif.add_argument("action", choices=["on", "off", "status"])

    sub.add_parser("remind-admins")
    sub.add_parser("slack-backfill")
    sub.add_parser("daily")  # cron entrypoint: backfill every day, remind admins on Sundays

    args = parser.parse_args()

    repo = Repository(default_db_path())

    if args.command == "players" and args.action == "list":
        players_list = repo.list_players(include_deleted=True)
        for player in players_list:
            status = "deleted" if player.deleted else "participating" if player.active else "sitting out"
            print(f"{player.id}: {player.name} ({status})")
    elif args.command == "players" and args.action == "add":
        repo.add_player(args.name)
        print("Player added.")
    elif args.command == "generate":
        active = repo.list_pairing_pool()
        if len(active) < 2:
            print("Need at least 2 active players.")
            return
        stats = repo.fetch_pairing_stats()
        prefs = repo.fetch_preferences()
        rng_seed = random.SystemRandom().randint(0, 2**31 - 1)
        draft = generate_best(
            [p.id for p in active],
            stats,
            DEFAULT_SCORING,
            DEFAULT_GENERATION,
            seed=rng_seed,
            prefs=prefs,
        )
        repo.clear_drafts()
        session_id = repo.create_draft(draft.seed, draft.score, draft.groups)
        print(f"Draft generated (session {session_id}, score {draft.score:.2f}).")
    elif args.command == "draft" and args.action == "show":
        draft = repo.get_draft()
        if not draft:
            print("No draft found.")
            return
        player_map = {p.id: p.name for p in repo.list_players(include_deleted=True)}
        _print_groups([g.member_ids for g in draft.groups], player_map)
        print(f"Score: {draft.score:.2f}")
    elif args.command == "draft" and args.action == "confirm":
        draft = repo.get_draft()
        if not draft:
            print("No draft to confirm.")
            return
        stats = repo.fetch_pairing_stats()
        breakdown = score_groups([g.member_ids for g in draft.groups], stats, DEFAULT_SCORING)
        repo.confirm_draft(draft.session_id, breakdown.total)
        print("Draft confirmed.")
    elif args.command == "history":
        sessions = repo.list_confirmed_sessions()
        for s in sessions:
            print(f"{s.id}: {s.created_at:%Y-%m-%d %H:%M} score={s.score:.2f}")
    elif args.command == "reset":
        if args.scope == "history":
            repo.reset_history()
            print("History reset.")
        else:
            repo.full_reset()
            print("Full reset complete.")
    elif args.command == "role" and args.action == "set":
        from portal.auth import store

        store.set_role(args.email, args.role)
        print(f"{args.email.strip().lower()} is now {args.role}.")
    elif args.command == "role" and args.action == "list":
        from portal.auth import store

        for email, role_name in store.list_roles():
            print(f"{email}: {role_name}")
    elif args.command == "link":
        from portal.auth import store

        player = repo.get_player(args.player_id)
        store.link_player_email(args.email, args.player_id)
        print(f"{args.email.strip().lower()} linked to {player.name} (id {player.id}).")
    elif args.command == "notifications":
        from portal import notifications

        if args.action == "status":
            state = "on" if notifications.notifications_enabled() else "off"
            print(f"Notifications are {state}.")
        else:
            notifications.set_notifications_enabled(args.action == "on")
            print(f"Notifications turned {args.action}.")
    elif args.command == "slack-backfill":
        from portal.features.slack_events.routes import backfill_from_channel

        print("Slack backfill:", backfill_from_channel())
    elif args.command == "daily":
        from datetime import datetime, timezone

        from portal import notifications
        from portal.features.slack_events.routes import backfill_from_channel

        print("Slack backfill:", backfill_from_channel())
        if datetime.now(timezone.utc).weekday() == 6 and notifications.notifications_enabled():
            result = notifications.send_admin_reminder()
            print(f"Admin reminder: {result['admins']} admins, {result['sent']} sent, {result['dry_run']} dry-run.")
        else:
            print("Admin reminder: not Sunday (or notifications off) — skipped.")
    elif args.command == "remind-admins":
        from portal import notifications

        if not notifications.notifications_enabled():
            print("Notifications are off — skipping admin reminder. "
                  "Enable with: python -m portal.cli notifications on")
            return
        result = notifications.send_admin_reminder()
        print(
            f"Admin reminder: {result['admins']} admins, "
            f"{result['sent']} sent, {result['dry_run']} dry-run."
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
