import heapq
import math
from functools import lru_cache


def dijkstra(graph, source):
    """Graph greedy: min-heap shortest paths in O((V + E) log V)."""
    distances = {node: math.inf for node in graph}
    previous = {node: None for node in graph}
    distances[source] = 0
    queue = [(0, source)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        for neighbor, weight in graph[node]:
            candidate = distance + weight
            if candidate < distances[neighbor]:
                distances[neighbor] = candidate
                previous[neighbor] = node
                heapq.heappush(queue, (candidate, neighbor))
    return distances, previous


def floyd_warshall(graph):
    """Graph dynamic programming: all-pairs paths in O(V^3)."""
    nodes = list(graph)
    distance = {left: {right: math.inf for right in nodes} for left in nodes}
    for node in nodes:
        distance[node][node] = 0
    for source, edges in graph.items():
        for target, weight in edges:
            distance[source][target] = min(distance[source][target], weight)
    for middle in nodes:
        for source in nodes:
            for target in nodes:
                distance[source][target] = min(
                    distance[source][target],
                    distance[source][middle] + distance[middle][target],
                )
    return distance


def _edge(matrix, left, right):
    if left == right:
        return {"minutes": 0.0, "meters": 0.0, "estimated": False}
    value = matrix.get(left, {}).get(right)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return {"minutes": float(value), "meters": float(value), "estimated": False}
    return value


def _departure_minute(riders):
    return max(
        max(rider["request_minute"] for rider in riders),
        max((rider.get("current_minute", 0.0) for rider in riders), default=0.0),
    )


def route_metrics(riders, order, matrix, start_node=None, leg_metrics=None):
    """Validate stop order, time windows, capacity and rider detour limits in O(r)."""
    if not riders:
        return None
    by_id = {rider["id"]: rider for rider in riders}
    departure = _departure_minute(riders)
    current = start_node
    minutes = 0.0
    meters = 0.0
    picked_at = {}
    onboard = {}
    waits = {}
    ride_times = {}
    estimated = False
    for stop_index, stop in enumerate(order):
        rider = by_id.get(stop["ride_id"])
        if rider is None:
            return None
        if leg_metrics is not None:
            if stop_index >= len(leg_metrics):
                return None
            leg = leg_metrics[stop_index]
        else:
            leg = _edge(matrix, current, stop["place_id"]) if current is not None else {
                "minutes": 0.0, "meters": 0.0, "estimated": False
            }
        if leg is None:
            return None
        minutes += float(leg["minutes"])
        meters += float(leg["meters"])
        estimated = estimated or bool(leg.get("estimated", False))
        current = stop["place_id"]
        if stop["action"] == "pickup":
            waits[rider["id"]] = departure + minutes - rider["request_minute"]
            if waits[rider["id"]] > rider["max_wait_minutes"]:
                return None
            picked_at[rider["id"]] = minutes
            onboard[rider["id"]] = rider["seats"]
            if sum(onboard.values()) > 4:
                return None
        elif stop["action"] == "drop":
            if rider["id"] not in onboard:
                return None
            elapsed = minutes - picked_at[rider["id"]]
            if elapsed > rider["solo_minutes"] * rider["max_detour"] + 1e-8:
                return None
            deadline = rider.get("leave_by_minute")
            if deadline is not None and departure + minutes > deadline + 1e-8:
                return None
            ride_times[rider["id"]] = elapsed
            del onboard[rider["id"]]
        else:
            return None
    if onboard or len(order) != 2 * len(riders):
        return None
    return {
        "minutes": minutes,
        "meters": meters,
        "waits": waits,
        "ride_times": ride_times,
        "estimated": estimated,
        "departure_minute": departure,
    }


def best_route_backtracking(riders, matrix, start_node=None):
    """Backtracking: feasible pickup/drop ordering with pruning, O((2r)!)."""
    if not riders:
        return None
    count = len(riders)
    full_mask = (1 << (2 * count)) - 1
    departure = _departure_minute(riders)
    best_meters = math.inf
    best_order = None
    best_result = None
    order = []
    onboard = {}
    picked_at = {}

    def search(mask, current, minutes, meters, estimated):
        nonlocal best_meters, best_order, best_result
        if meters >= best_meters:
            return
        if mask == full_mask and not onboard:
            result = route_metrics(riders, order, matrix, start_node)
            if result is not None and result["meters"] < best_meters:
                best_meters = result["meters"]
                best_order = list(order)
                best_result = result
            return
        for index, rider in enumerate(riders):
            pickup_bit = 1 << index
            drop_bit = 1 << (index + count)
            if not (mask & pickup_bit):
                if sum(onboard.values()) + rider["seats"] > 4:
                    continue
                leg = _edge(matrix, current, rider["pickup_place_id"]) if current is not None else {
                    "minutes": 0.0, "meters": 0.0, "estimated": False
                }
                if leg is None:
                    continue
                next_minutes = minutes + float(leg["minutes"])
                wait = departure + next_minutes - rider["request_minute"]
                if wait > rider["max_wait_minutes"]:
                    continue
                next_meters = meters + float(leg["meters"])
                if next_meters >= best_meters:
                    continue
                stop = {"ride_id": rider["id"], "action": "pickup", "place_id": rider["pickup_place_id"]}
                order.append(stop)
                picked_at[rider["id"]] = next_minutes
                onboard[rider["id"]] = rider["seats"]
                search(
                    mask | pickup_bit, rider["pickup_place_id"], next_minutes,
                    next_meters, estimated or bool(leg.get("estimated", False)),
                )
                del onboard[rider["id"]]
                del picked_at[rider["id"]]
                order.pop()
            elif (mask & pickup_bit) and not (mask & drop_bit) and rider["id"] in onboard:
                leg = _edge(matrix, current, rider["drop_place_id"]) if current is not None else {
                    "minutes": 0.0, "meters": 0.0, "estimated": False
                }
                if leg is None:
                    continue
                next_minutes = minutes + float(leg["minutes"])
                if next_minutes - picked_at[rider["id"]] > rider["solo_minutes"] * rider["max_detour"] + 1e-8:
                    continue
                deadline = rider.get("leave_by_minute")
                if deadline is not None and departure + next_minutes > deadline + 1e-8:
                    continue
                next_meters = meters + float(leg["meters"])
                if next_meters >= best_meters:
                    continue
                stop = {"ride_id": rider["id"], "action": "drop", "place_id": rider["drop_place_id"]}
                order.append(stop)
                del onboard[rider["id"]]
                search(
                    mask | drop_bit, rider["drop_place_id"], next_minutes,
                    next_meters, estimated or bool(leg.get("estimated", False)),
                )
                onboard[rider["id"]] = rider["seats"]
                order.pop()

    search(0, start_node, 0.0, 0.0, False)
    if best_order is None:
        return None
    return {"order": best_order, **best_result}


def held_karp_route(riders, matrix, start_node=None):
    """Held-Karp dynamic programming on 2r stops in O(m^2 * 2^m), m = 2r."""
    if not riders:
        return None
    stops = []
    for rider in riders:
        stops.extend([
            {"ride_id": rider["id"], "action": "pickup", "place_id": rider["pickup_place_id"]},
            {"ride_id": rider["id"], "action": "drop", "place_id": rider["drop_place_id"]},
        ])
    size = len(stops)
    dp = {}
    parent = {}
    for index, stop in enumerate(stops):
        if stop["action"] != "pickup":
            continue
        leg = _edge(matrix, start_node, stop["place_id"]) if start_node is not None else {
            "minutes": 0.0, "meters": 0.0, "estimated": False
        }
        if leg is None:
            continue
        rider = next(item for item in riders if item["id"] == stop["ride_id"])
        wait = _departure_minute(riders) + leg["minutes"] - rider["request_minute"]
        if wait <= rider["max_wait_minutes"]:
            dp[(1 << index, index)] = float(leg["meters"])
    for mask in range(1 << size):
        for last in range(size):
            if (mask, last) not in dp:
                continue
            for nxt, stop in enumerate(stops):
                bit = 1 << nxt
                if mask & bit:
                    continue
                rider_index = nxt // 2
                pickup_index, drop_index = rider_index * 2, rider_index * 2 + 1
                if nxt == drop_index and not mask & (1 << pickup_index):
                    continue
                onboard_seats = sum(
                    riders[index]["seats"] for index in range(len(riders))
                    if mask & (1 << (2 * index)) and not mask & (1 << (2 * index + 1))
                )
                if nxt == pickup_index and onboard_seats + riders[rider_index]["seats"] > 4:
                    continue
                leg = _edge(matrix, stops[last]["place_id"], stop["place_id"])
                if leg is None:
                    continue
                new_mask = mask | bit
                key = (new_mask, nxt)
                candidate = dp[(mask, last)] + float(leg["meters"])
                if candidate < dp.get(key, math.inf):
                    dp[key] = candidate
                    parent[key] = (mask, last)
    full = (1 << size) - 1
    final_states = sorted(
        (dp[(full, last)], last) for last in range(size) if (full, last) in dp
    )
    for _, last in final_states:
        state = (full, last)
        indexes = []
        while state in parent:
            indexes.append(state[1])
            state = parent[state]
        indexes.append(state[1])
        indexes.reverse()
        order = [stops[index] for index in indexes]
        result = route_metrics(riders, order, matrix, start_node)
        if result is not None:
            return {"order": order, **result}
    return None


def greedy_grouping(riders, matrix):
    """Greedy insertion: least added route cost, O(n^2) group attempts."""
    groups = []
    for rider in sorted(riders, key=lambda item: (item["request_minute"], item["id"])):
        choice = None
        for index, group in enumerate(groups):
            if sum(item["seats"] for item in group["riders"]) + rider["seats"] > 4:
                continue
            route = best_route_backtracking(group["riders"] + [rider], matrix)
            if route is None:
                continue
            previous = group["route"]["meters"]
            added = route["meters"] - previous
            if choice is None or added < choice[0]:
                choice = (added, index, route)
        if choice is None:
            route = best_route_backtracking([rider], matrix)
            if route is None:
                raise ValueError("A rider cannot meet the wait and detour limits alone.")
            groups.append({"riders": [rider], "route": route})
        else:
            _, index, route = choice
            groups[index]["riders"].append(rider)
            groups[index]["route"] = route
    return groups


def optimal_grouping(riders, matrix):
    """Bitmask dynamic programming set partitioning in O(3^n), for n <= 10."""
    n = len(riders)
    if n > 10:
        raise ValueError("Exact grouping accepts at most 10 requests per cluster.")
    cache = {}

    def candidate(mask):
        if mask not in cache:
            members = [riders[i] for i in range(n) if mask & (1 << i)]
            if sum(item["seats"] for item in members) > 4:
                cache[mask] = None
            else:
                cache[mask] = best_route_backtracking(members, matrix)
        return cache[mask]

    @lru_cache(maxsize=None)
    def partition(mask):
        if not mask:
            return 0.0, ()
        first = mask & -mask
        best = (math.inf, ())
        sub = mask
        while sub:
            if sub & first:
                route = candidate(sub)
                if route is not None:
                    rest_cost, rest_groups = partition(mask ^ sub)
                    total = route["meters"] + rest_cost
                    if total < best[0]:
                        best = total, (sub,) + rest_groups
            sub = (sub - 1) & mask
        return best

    cost, masks = partition((1 << n) - 1)
    if not math.isfinite(cost):
        raise ValueError("No compatible grouping meets the rider constraints.")
    return [
        {
            "riders": [riders[index] for index in range(n) if mask & (1 << index)],
            "route": candidate(mask),
        }
        for mask in masks
    ]


def hungarian(costs):
    """Assignment: Hungarian potentials algorithm in O(n^3) for a square matrix."""
    n = len(costs)
    if not n:
        return []
    if any(len(row) != n for row in costs):
        raise ValueError("Hungarian assignment needs a square matrix.")
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    matching = [0] * (n + 1)
    previous = [0] * (n + 1)
    for row in range(1, n + 1):
        matching[0] = row
        column0 = 0
        minimum = [math.inf] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[column0] = True
            row0 = matching[column0]
            delta = math.inf
            column1 = 0
            for column in range(1, n + 1):
                if used[column]:
                    continue
                reduced = costs[row0 - 1][column - 1] - u[row0] - v[column]
                if reduced < minimum[column]:
                    minimum[column] = reduced
                    previous[column] = column0
                if minimum[column] < delta:
                    delta = minimum[column]
                    column1 = column
            for column in range(n + 1):
                if used[column]:
                    u[matching[column]] += delta
                    v[column] -= delta
                else:
                    minimum[column] -= delta
            column0 = column1
            if matching[column0] == 0:
                break
        while True:
            column1 = previous[column0]
            matching[column0] = matching[column1]
            column0 = column1
            if column0 == 0:
                break
    result = [0] * n
    for column in range(1, n + 1):
        result[matching[column] - 1] = column - 1
    return result


def nearest_assignment(costs, invalid_cost=1e15):
    """Greedy assignment: nearest available cab per group in O(g*c)."""
    if not costs:
        return []
    available = set(range(len(costs[0])))
    result = []
    for row in costs:
        choices = [index for index in available if row[index] < invalid_cost]
        if not choices:
            result.append(None)
            continue
        selected = min(choices, key=lambda index: row[index])
        available.remove(selected)
        result.append(selected)
    return result
