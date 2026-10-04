import heapq

def shortest_path(edges: list[str], start: str, goal: str) -> int:
    graph: dict[str, list[tuple[str, int]]] = {}
    for e in edges:
        try:
            ends, w = e.split(":")
            a, b = ends.split("-")
            weight = int(w)
        except ValueError:
            raise ValueError(f"bad edge {e}")
        if weight < 0 or not a or not b:
            raise ValueError(f"bad edge {e}")
        graph.setdefault(a, []).append((b, weight))
        graph.setdefault(b, []).append((a, weight))
    dist = {start: 0}
    heap = [(0, start)]
    while heap:
        d, n = heapq.heappop(heap)
        if n == goal:
            return d
        if d > dist.get(n, float("inf")):
            continue
        for m, w in graph.get(n, []):
            if d + w < dist.get(m, float("inf")):
                dist[m] = d + w
                heapq.heappush(heap, (d + w, m))
    raise ValueError("unreachable")
