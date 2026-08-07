from __future__ import annotations

import argparse
import random
from pathlib import Path

from lunchtag.config import default_db_path
from lunchtag.engine.config import DEFAULT_GENERATION, DEFAULT_SCORING
from lunchtag.engine.generator import generate_best
from lunchtag.engine.scoring import score_groups
from lunchtag.storage.repo import Repository


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

    args = parser.parse_args()

    repo = Repository(default_db_path())

    if args.command == "players" and args.action == "list":
        players_list = repo.list_players(include_deleted=True)
        for player in players_list:
            status = "deleted" if player.deleted else "active" if player.active else "inactive"
            print(f"{player.id}: {player.name} ({status})")
    elif args.command == "players" and args.action == "add":
        repo.add_player(args.name)
        print("Player added.")
    elif args.command == "generate":
        active = repo.list_active_players()
        if len(active) < 2:
            print("Need at least 2 active players.")
            return
        stats = repo.fetch_pairing_stats()
        rng_seed = random.SystemRandom().randint(0, 2**31 - 1)
        draft = generate_best(
            [p.id for p in active],
            stats,
            DEFAULT_SCORING,
            DEFAULT_GENERATION,
            seed=rng_seed,
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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
