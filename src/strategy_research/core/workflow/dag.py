from __future__ import annotations

from collections import deque


def validate_dag(adj: dict[str, list[str]]) -> None:
    all_nodes: set[str] = set(adj)
    for targets in adj.values():
        all_nodes.update(targets)

    in_degree: dict[str, int] = {n: 0 for n in all_nodes}
    for src, targets in adj.items():
        for tgt in targets:
            in_degree[tgt] = in_degree.get(tgt, 0) + 1

    queue: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
    visited = 0

    while queue:
        node = queue.popleft()
        visited += 1
        for tgt in adj.get(node, []):
            in_degree[tgt] -= 1
            if in_degree[tgt] == 0:
                queue.append(tgt)

    if visited != len(all_nodes):
        raise ValueError("DAG contains a cycle")


def topological_layers(adj: dict[str, list[str]]) -> list[list[str]]:
    all_nodes: set[str] = set(adj)
    for targets in adj.values():
        all_nodes.update(targets)

    in_degree: dict[str, int] = {n: 0 for n in all_nodes}
    for src, targets in adj.items():
        for tgt in targets:
            in_degree[tgt] = in_degree.get(tgt, 0) + 1

    queue: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
    layers: list[list[str]] = []

    while queue:
        layer = list(queue)
        layers.append(sorted(layer))
        queue.clear()
        for node in layer:
            for tgt in adj.get(node, []):
                in_degree[tgt] -= 1
                if in_degree[tgt] == 0:
                    queue.append(tgt)

    return layers
