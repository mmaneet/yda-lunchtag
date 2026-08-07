from __future__ import annotations

from typing import Iterable


def swap_players(groups: Iterable[Iterable[int]], player_a: int, player_b: int) -> tuple[tuple[int, ...], ...]:
    new_groups: list[list[int]] = []
    for group in groups:
        members = list(group)
        if player_a in members and player_b in members:
            new_groups.append(members)
            continue
        if player_a in members:
            members[members.index(player_a)] = player_b
        elif player_b in members:
            members[members.index(player_b)] = player_a
        new_groups.append(members)
    return tuple(tuple(group) for group in new_groups)
