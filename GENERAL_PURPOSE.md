# Jig: A General-Purpose Programming Language

**Not just for APIs - for building ANYTHING**

## The Complete Picture

Jig is a **full general-purpose programming language** where you can build:
- ✅ Algorithms (sorting, searching, graph algorithms)
- ✅ Data structures (stacks, trees, graphs, queues)
- ✅ CLI tools and utilities
- ✅ Web servers and APIs
- ✅ Data processing pipelines
- ✅ Games and simulations
- ✅ Desktop applications
- ✅ Any computational task

**The LLM-first features apply to ALL code, not just external APIs.**

---

## Misconception vs Reality

### ❌ Misconception
"Jig is for wrapping external APIs like Stripe"

### ✅ Reality
"Jig is for writing ANY code. The LLM-first features (manifests, decisions, patterns, cache) help with ALL programming."

---

## How LLM-First Features Apply Generically

### 1. **Manifests** = Module Interfaces (Not Just External APIs)

**Use Case 1: External APIs**
```
# lib/stripe.manifest
declare charge(amount: int, customer: str) -> Result[ChargeId, Error]
```

**Use Case 2: Your Own Modules**
```
# lib/graph.manifest
declare shortest_path(graph: Graph, start: NodeId, end: NodeId) -> Option[list[NodeId]]
declare topological_sort(graph: Graph) -> Result[list[NodeId], str]
```

**Use Case 3: Algorithm Library**
```
# lib/search.manifest
declare binary_search(items: list[int], target: int) -> Option[int]
declare linear_search(items: list[int], target: int) -> Option[int]
```

**The point**: Manifests define **any** module boundary. LLM can't hallucinate methods on **your** data structures either.

```python
from lib.graph import dijkstra  # ❌ R003: no export 'dijkstra'
                                #    Available: shortest_path, topological_sort
```

---

### 2. **Decisions** = Any Architectural Choice

**Not limited to "Money uses int":**

#### Example: Data Structure Decisions
```
# .decisions/immutability.decision
decision: All data structures are immutable. Use records, not classes.
rationale: Immutability prevents shared mutable state bugs.

# Now LLM can't use mutable classes
class Stack:  # ❌ DEC001: violates decision 'immutability'
    def __init__(self): ...

record Stack: items: list[int]  # ✅
```

#### Example: Algorithm Decisions
```
# .decisions/loops.decision
decision: All algorithms use bounded for loops, never while or recursion
rationale: Prevents infinite loops and stack overflow

# Now LLM can't use unbounded loops
while condition:  # ❌ F009: while not allowed
for i in range(n):  # ✅
```

#### Example: Error Handling Decisions
```
# .decisions/errors.decision
decision: All errors are Result types, never exceptions
rationale: Explicit error handling, no hidden control flow

# Now LLM can't use exceptions
try: ...  # ❌ F007: exceptions not allowed
def f() -> Result[T, E]:  # ✅
```

#### Example: ID Decisions
```
# .decisions/identifiers.decision
decision: All entity IDs are UUIDs (str), not auto-increment (int)
rationale: Works across distributed systems

type UserId = UserId(int)  # ❌ DEC001: violates decision
type UserId = UserId(str)  # ✅
```

**The point**: Decisions enforce **any** architectural choice in **any** domain.

---

### 3. **Patterns** = Generic Programming Patterns

**Not just "retry with backoff":**

#### Pattern: Binary Search
```
# .patterns/binary-search.pattern
problem: Searching in sorted list
solution:
    def binary_search(items: list[int], target: int) -> Option[int]:
        low = 0
        high = len(items) - 1
        for _ in range(len(items)):  # Bounded
            if low > high: return Nothing
            mid = (low + high) // 2
            # ...
```

#### Pattern: Tree Traversal
```
# .patterns/tree-traversal.pattern
problem: Traversing tree structures
solution:
    def traverse(node: Node) -> list[int]:
        stack = [node]
        result: list[int] = []
        for _ in range(MAX_NODES):  # Bounded, no recursion
            if len(stack) == 0: break
            current = stack.pop()
            # ...
```

#### Pattern: State Machine
```
# .patterns/state-machine.pattern
problem: Managing state transitions
solution:
    def handle_event(state: State, event: Event) -> State:
        match (state, event):
            case (State.IDLE, Event.START): return State.RUNNING
            case (State.RUNNING, Event.STOP): return State.IDLE
            # Exhaustive match required
```

#### Pattern: Fold/Reduce
```
# .patterns/fold.pattern
problem: Reducing a list to a single value
solution:
    def fold(items: list[int], initial: int, op: str) -> int:
        result = initial
        for item in items:
            match op:
                case "sum": result = result + item
                case "product": result = result * item
        return result
```

**The point**: Patterns define "the one way" for **any** common task.

---

### 4. **Generation Cache** = Prevents Drift in Any Code

**Not just API wrappers:**

```python
# Spec: quicksort function
# - Signature: quicksort(items: list[int]) -> list[int]
# - Contract: requires len(items) < 10000
# - Examples: quicksort([3,1,4,1,5]) -> [1,1,3,4,5]

# First generation:
def quicksort(items: list[int]) -> list[int]:
    # Implementation A...

# Content hash: 7a3f9e2c
# Cached

# Next time same spec appears → Retrieved from cache
# Same algorithm always implemented the same way
```

**Applies to:**
- Sorting algorithms
- Search algorithms
- Tree operations
- Graph algorithms
- State machines
- Data transformations
- **Any** function

**The point**: Same spec = same implementation for **any** code.

---

## Real Example: Complete Data Structure Library

**Zero API dependencies, pure computation:**

```python
# Stack data structure (examples/datastructures/stack.jig)
module datastructures.stack

record Stack:
    items: list[int]
    capacity: int

def empty_stack(capacity: int) -> Stack
def push(stack: Stack, value: int) -> Option[Stack]
def pop(stack: Stack) -> Option[tuple[int, Stack]]
def peek(stack: Stack) -> Option[int]
def is_empty(stack: Stack) -> bool
def is_full(stack: Stack) -> bool

# ✅ All examples pass: 12/12
```

**LLM-first features help:**
- **Decision**: "Data structures are immutable records"
- **Pattern**: "Stack operations return new stack via Option"
- **Manifest** (if in lib/): Defines public interface
- **Cache**: Same stack spec = same implementation

---

## More Complete Examples

### Example: Graph Algorithm Library

```python
module algorithms.graph

type NodeId = NodeId(int)
type Weight = Weight(int)

record Edge:
    from_node: NodeId
    to_node: NodeId
    weight: Weight

record Graph:
    nodes: list[NodeId]
    edges: list[Edge]


def shortest_path(graph: Graph, start: NodeId, end: NodeId) -> Option[list[NodeId]]:
    """Dijkstra's algorithm."""
    effects: none
    requires: len(graph.nodes) < 10000
    examples:
        shortest_path(simple_graph(), NodeId(1), NodeId(4)) -> Some([NodeId(1), NodeId(2), NodeId(4)])
    # Implementation...


def is_cyclic(graph: Graph) -> bool:
    """Check if graph has cycles using DFS."""
    effects: none
    examples:
        is_cyclic(acyclic_graph()) -> False
        is_cyclic(cyclic_graph()) -> True
    # Implementation...


def topological_sort(graph: Graph) -> Result[list[NodeId], str]:
    """Topological sort (errors if cyclic)."""
    effects: none
    examples:
        topological_sort(dag()) -> Ok([NodeId(1), NodeId(2), NodeId(3)])
        topological_sort(cyclic_graph()) -> Err("graph contains cycle")
    # Implementation...
```

**LLM-first features:**
- **Decision**: "Graph edges use Weight newtype"
- **Decision**: "Algorithms use bounded iteration"
- **Pattern**: "DFS uses explicit stack, not recursion"

---

### Example: Text Processing

```python
module text.parser

record Token:
    type: str
    value: str
    position: int

enum ParseError:
    UNEXPECTED_TOKEN
    UNEXPECTED_EOF
    INVALID_SYNTAX


def tokenize(text: str) -> list[Token]:
    """Split text into tokens."""
    effects: none
    requires: len(text) < 1000000
    examples:
        tokenize("hello world") -> [Token("word", "hello", 0), Token("word", "world", 6)]
    # Implementation...


def parse_expression(tokens: list[Token]) -> Result[int, ParseError]:
    """Parse arithmetic expression."""
    effects: none
    examples:
        parse_expression([Token("num", "5", 0)]) -> Ok(5)
        parse_expression([Token("lparen", "(", 0)]) -> Err(ParseError.UNEXPECTED_EOF)
    # Implementation...
```

**LLM-first features:**
- **Decision**: "Parser errors use Result, not exceptions"
- **Decision**: "Tokens are immutable records"
- **Pattern**: "Recursive descent with bounded depth"

---

### Example: Game Logic

```python
module game.chess

type Position = Position(str)  # e.g. "e4"

enum Piece:
    PAWN
    KNIGHT
    BISHOP
    ROOK
    QUEEN
    KING

record Move:
    piece: Piece
    from_pos: Position
    to_pos: Position

record Board:
    pieces: dict[Position, Piece]
    turn: str  # "white" or "black"


def is_valid_move(board: Board, move: Move) -> bool:
    """Check if move is legal."""
    effects: none
    examples:
        is_valid_move(starting_board(), Move(Piece.PAWN, Position("e2"), Position("e4"))) -> True
        is_valid_move(starting_board(), Move(Piece.PAWN, Position("e2"), Position("e5"))) -> False
    # Implementation...


def apply_move(board: Board, move: Move) -> Option[Board]:
    """Apply move if valid. Returns Nothing if invalid."""
    effects: none
    examples:
        apply_move(starting_board(), valid_move()) -> Some(new_board)
        apply_move(starting_board(), invalid_move()) -> Nothing
    # Implementation...
```

**LLM-first features:**
- **Decision**: "Board state is immutable"
- **Decision**: "Position uses str (algebraic notation)"
- **Pattern**: "Move validation uses exhaustive matching"

---

## What You Can Build

| Domain | Example | LLM-First Features Help |
|--------|---------|------------------------|
| **Algorithms** | Sorting, searching, graph algorithms | Decisions: bounded loops. Patterns: iterative implementations. Cache: same algorithm spec = same code |
| **Data Structures** | Trees, graphs, stacks, queues | Decisions: immutability. Patterns: canonical operations. Manifests: public interface |
| **Text Processing** | Parsers, compilers, formatters | Decisions: Result for errors. Patterns: recursive descent. Examples: parse test cases |
| **Games** | Chess, tic-tac-toe, simulations | Decisions: immutable state. Patterns: state machines. Examples: game rules as tests |
| **CLI Tools** | File processors, converters | Decisions: argument parsing. Patterns: command handling. Examples: CLI test cases |
| **Web Servers** | APIs, HTTP handlers | Decisions: endpoint patterns. Patterns: request/response. Manifests: route definitions |
| **Data Analysis** | Stats, aggregation, ML | Decisions: numeric types. Patterns: fold/map/filter. Examples: calculation tests |
| **Cryptography** | Hashing, encryption | Decisions: bytes for binary data. Patterns: safe implementations. Examples: test vectors |

---

## The Development Workflow (Generic)

### Scenario: Implement Quicksort

**Step 1: Check patterns**
```bash
$ cat .patterns/quicksort.pattern
# Shows canonical implementation
```

**Step 2: Check decisions**
```bash
$ cat .decisions/algorithms.decision
# "Use iterative or bounded recursion"
$ cat .decisions/loops.decision
# "Use for with range(), not while"
```

**Step 3: Write code**
```python
def quicksort(items: list[int]) -> list[int]:
    """Sort using quicksort."""
    effects: none
    requires: len(items) < 10000  # Bounded
    examples:
        quicksort([3, 1, 4, 1, 5]) -> [1, 1, 3, 4, 5]
        quicksort([]) -> []
    
    if len(items) <= 1:
        return items
    
    pivot = items[len(items) // 2]
    left = [x for x in items if x < pivot]
    middle = [x for x in items if x == pivot]
    right = [x for x in items if x > pivot]
    
    return quicksort(left) + middle + quicksort(right)
```

**Step 4: Check**
```bash
$ jig check algorithms/sorting.jig --pretty
✅ ok: 0 errors, 0 warnings, examples 2/2 passed
```

**Step 5: Cached**
```
Content hash: 8f3a7e2c9d1b
Stored in .jig-cache/generated/
Next time same spec → retrieved
```

---

## Key Insight

The LLM-first features are **domain-agnostic**:

- **Manifests** = Define **any** interface (API, library, module)
- **Decisions** = Enforce **any** architectural choice (types, patterns, conventions)
- **Patterns** = Canonical implementation of **any** common task (algorithms, data structures, workflows)
- **Cache** = Prevent drift in **any** generated code (algorithms, business logic, transformations)

**Result**: Build **anything** with mechanical enforcement of correctness.

---

## What Makes Jig Different

| Language | Purpose | Enforcement |
|----------|---------|-------------|
| **Python** | General-purpose | Hope & convention |
| **TypeScript** | General-purpose with types | Type checking |
| **Rust** | Systems programming | Borrow checker |
| **Haskell** | Functional programming | Type system + purity |
| **Jig** | **LLM-first general-purpose** | Manifests + Decisions + Patterns + Cache + Types + Effects + Contracts + Examples |

Jig adds **LLM-specific enforcement** on top of traditional type/contract checking:
- Can't hallucinate (manifests)
- Can't forget decisions (decision notebook)
- Can't improvise (patterns)
- Can't drift (generation cache)

---

## Conclusion

**Jig is NOT "just for APIs"**

It's a **complete general-purpose programming language** where:
- ✅ You can build **anything** (algorithms, data structures, apps, games, tools)
- ✅ LLM-first features apply to **all code** (not just API wrappers)
- ✅ **No compromise** on what you can express
- ✅ Mechanical enforcement prevents LLM mistakes in **any** domain

**The vision**: A language where code that compiles is provably correct, regardless of what you're building.

---

Jig - **General-Purpose Programming, Built for LLMs**
