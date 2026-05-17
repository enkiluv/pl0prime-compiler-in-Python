"""
PL/0' Compiler  —  Symbol Table
================================
The symbol table is a flat array with a *stack discipline*.
Every time a new block opens (block_begin), the current high-water marks
are saved on internal stacks; block_end restores them.

Entry types
-----------
  ConstEntry   —  named constant         (kind = CONST_ID)
  AddrEntry    —  variable or parameter  (kind = VAR_ID / PAR_ID)
  FuncEntry    —  function               (kind = FUNC_ID)

Address layout within a stack frame
------------------------------------
  frame[0]          : saved display entry (overwritten by callee)
  frame[1]          : return address
  frame[2..]        : local variables  (FIRST_ADDR = 2 in compiler.py)
  frame[-n..-1]     : n parameters (negative offsets; fixed by end_params)
"""

from dataclasses import dataclass, field
from get_source import GetSource, CONST_ID, VAR_ID, PAR_ID, FUNC_ID


# ---------------------------------------------------------------------------
# RelAddr: a (level, offset) pair used at run time to locate a variable
# ---------------------------------------------------------------------------

@dataclass
class RelAddr:
    """
    Relative address within a block's stack frame.

    level  : nesting depth of the block that owns the variable
    addr   : offset from the frame's base pointer (display[level])
    """
    level: int
    addr:  int

    def __str__(self) -> str:
        return f"{self.level},{self.addr}"


# ---------------------------------------------------------------------------
# Symbol-table entry classes
# ---------------------------------------------------------------------------

@dataclass
class ConstEntry:
    """Symbol-table entry for a named constant  (const x = 42)."""
    name:  str = ""
    kind:  int = CONST_ID
    value: int = 0

    def __str__(self) -> str:
        return f"{self.name}:const:{self.value}"


@dataclass
class AddrEntry:
    """Symbol-table entry for a variable or parameter."""
    name:     str     = ""
    kind:     int     = VAR_ID
    rel_addr: RelAddr = field(default_factory=lambda: RelAddr(0, 0))

    def __str__(self) -> str:
        kind_name = {VAR_ID: "var", PAR_ID: "par"}.get(self.kind, "?")
        return f"{self.name}:{kind_name}:{self.rel_addr}"


@dataclass
class FuncEntry:
    """Symbol-table entry for a function (carries a parameter count)."""
    name:       str     = ""
    kind:       int     = FUNC_ID
    rel_addr:   RelAddr = field(default_factory=lambda: RelAddr(0, 0))
    num_params: int     = 0

    def __str__(self) -> str:
        return f"{self.name}:func:{self.rel_addr}:{self.num_params} params"


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

class Table:
    """
    Manages the symbol table throughout compilation.

    Lookup is linear from the top of the table downward, so inner-scope
    declarations shadow outer ones automatically.
    """

    MAXTABLE = 100   # maximum total symbol-table entries
    MAXLEVEL = 5     # maximum block nesting depth

    def __init__(self, source: GetSource, print_table: bool):
        """
        Parameters
        ----------
        source       : GetSource used for fatal-error reporting.
        print_table  : if True, dump each block's symbol table when it closes.
        """
        self._src          = source
        self._print_table  = print_table

        # Flat entry array.  Index 0 is reserved as a search sentinel.
        self._entries = [None] * (self.MAXTABLE + 1)
        self._top     = 0    # index of the most recently added entry
        self._level   = -1   # current block nesting level (-1 = not yet started)

        # Per-level bookmarks (saved when a new block opens)
        self._saved_top  = [0] * self.MAXLEVEL
        self._saved_addr = [0] * self.MAXLEVEL

        self._local_addr = 0    # next available variable offset in current frame
        self._func_idx   = 0    # table index of the most recently entered function

    # ------------------------------------------------------------------
    # Block management
    # ------------------------------------------------------------------

    def block_begin(self, first_addr: int) -> None:
        """
        Called at the start of a new block.

        *first_addr* is the frame offset where local variables start.
        For the main block and all function bodies this is 2 (slots 0 and 1
        are reserved for the saved display entry and return address).
        """
        if self._level == -1:
            # Top-level initialisation
            self._local_addr = first_addr
            self._top        = 0
            self._level      = 0
            return
        if self._level >= self.MAXLEVEL - 1:
            self._src.error_fatal("too many nested blocks")
        # Save the surrounding block's bookmarks
        self._saved_top [self._level] = self._top
        self._saved_addr[self._level] = self._local_addr
        # Start fresh for the new block
        self._local_addr = first_addr
        self._level     += 1

    def block_end(self) -> None:
        """
        Called at the end of a block.
        Optionally dumps the block's symbol table, then restores the
        surrounding block's state.
        """
        if self._print_table:
            print(f"\n ******** Symbol Table of level {self._level} ********")
            start = 1 if self._level == 0 else self._saved_top[self._level - 1] + 1
            for i in range(start, self._top + 1):
                print(self._entries[i])
        if self._level == 0:
            return
        self._level      -= 1
        self._top        = self._saved_top [self._level]
        self._local_addr = self._saved_addr[self._level]

    def block_level(self) -> int:
        """Return the nesting level of the block currently being compiled."""
        return self._level

    def func_params(self) -> int:
        """
        Return the parameter count of the function that owns the current block.
        Returns 0 for the main (top-level) block.
        """
        if self._level == 0:
            return 0
        return self._entries[self._saved_top[self._level - 1]].num_params

    def frame_size(self) -> int:
        """
        Return the number of stack slots needed for the current block's frame.
        This is the value passed to the ICT (increment-stack-top) instruction.
        """
        return self._local_addr

    # ------------------------------------------------------------------
    # Entry insertion
    # ------------------------------------------------------------------

    def _push(self, entry) -> int:
        """Add *entry* to the table and return its index."""
        self._top += 1
        if self._top <= self.MAXTABLE:
            self._entries[self._top] = entry
        else:
            self._src.error_fatal("too many names")
        return self._top

    def enter_func(self, name: str, tentative_addr: int) -> int:
        """
        Register a function name.

        *tentative_addr* is a placeholder entry address that will be
        corrected later by fix_func_addr() once the function's code
        address is known.

        Returns the table index (needed for fix_func_addr).
        """
        entry = FuncEntry(
            name=name,
            kind=FUNC_ID,
            rel_addr=RelAddr(self._level, tentative_addr),
            num_params=0,
        )
        idx = self._push(entry)
        self._func_idx = idx
        return idx

    def enter_param(self, name: str) -> int:
        """
        Register a parameter name and increment the owning function's
        parameter count.  The actual frame offset is fixed later by end_params().
        """
        entry = AddrEntry(
            name=name,
            kind=PAR_ID,
            rel_addr=RelAddr(self._level, 0),  # placeholder offset
        )
        idx = self._push(entry)
        self._entries[self._func_idx].num_params += 1
        return idx

    def enter_var(self, name: str) -> int:
        """
        Register a variable name and assign the next available local-frame offset.
        """
        entry = AddrEntry(
            name=name,
            kind=VAR_ID,
            rel_addr=RelAddr(self._level, self._local_addr),
        )
        self._local_addr += 1
        return self._push(entry)

    def enter_const(self, name: str, value: int) -> int:
        """Register a named constant."""
        return self._push(ConstEntry(name=name, kind=CONST_ID, value=value))

    def end_params(self) -> None:
        """
        Fix up the frame offsets of all parameters once the full parameter
        list has been parsed.

        Parameters are stored *below* the frame base (negative offsets).
        If the function has n parameters:
          - last parameter  → offset  -1
          - first parameter → offset  -n
        """
        n = self._entries[self._func_idx].num_params
        if n == 0:
            return
        for i in range(1, n + 1):
            self._entries[self._func_idx + i].rel_addr.addr = i - 1 - n

    def fix_func_addr(self, idx: int, new_addr: int) -> None:
        """Update the code-start address of the function at table index *idx*."""
        self._entries[idx].rel_addr.addr = new_addr

    # ------------------------------------------------------------------
    # Lookup and attribute access
    # ------------------------------------------------------------------

    def search(self, name: str, fallback_kind: int) -> int:
        """
        Search for *name*, scanning from the top of the table downward
        (most-recently-declared scope first).

        On success: return the entry's table index.
        On failure: report an 'undef' error; if *fallback_kind* is VAR_ID,
                    also insert a dummy entry so compilation can continue.
        """
        # Place a sentinel at index 0 so the loop always terminates.
        self._entries[0] = AddrEntry(name=name)
        i = self._top
        while self._entries[i].name != name:
            i -= 1
        if i > 0:
            return i
        # Not found
        self._src.error_type("undef")
        if fallback_kind == VAR_ID:
            return self.enter_var(name)   # dummy entry so compilation continues
        return 0

    def kind_at(self, idx: int) -> int:
        """Return the kind (CONST_ID / VAR_ID / PAR_ID / FUNC_ID) of entry *idx*."""
        return self._entries[idx].kind

    def rel_addr_at(self, idx: int) -> RelAddr:
        """Return the relative address stored in entry *idx*."""
        return self._entries[idx].rel_addr

    def value_at(self, idx: int) -> int:
        """Return the constant value stored in entry *idx*  (ConstEntry only)."""
        return self._entries[idx].value

    def params_at(self, idx: int) -> int:
        """Return the parameter count stored in entry *idx*  (FuncEntry only)."""
        return self._entries[idx].num_params
