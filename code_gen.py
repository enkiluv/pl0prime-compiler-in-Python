"""
PL/0' Compiler  —  Code Generator and Virtual Machine
======================================================
This module contains two closely related things:

1. **Code generator** (``CodeGen``) — emits instructions into a flat
   instruction array during compilation.

2. **Virtual machine** (``CodeGen.execute``) — runs the instruction array
   on a simple stack machine after compilation succeeds.

Instruction set
---------------
Every instruction is one of three shapes:

  InstLit(opcode, value)       LIT, ICT, JMP, JPC
  InstAddr(opcode, rel_addr)   LOD, STO, CAL, RET
  InstOpr(opcode, opr)         OPR (arithmetic / relational / I-O)

Runtime memory model
--------------------
  stack    : linear array of integers  (size MAXMEM)
  display  : display[l] = base address of the active frame at nesting level l

Frame layout (base = display[level])
  stack[base + 0]  : saved display[level] from caller
  stack[base + 1]  : return address (next pc in caller)
  stack[base + 2+] : local variables
  stack[base - n .. base - 1] : n parameters (negative offsets)
"""

import sys
from dataclasses import dataclass, field
from get_source import GetSource
from table import Table, RelAddr


# ---------------------------------------------------------------------------
# Opcode constants
# ---------------------------------------------------------------------------

class Op:
    """Opcode constants for the PL/0' virtual machine."""
    LIT = 1    # push literal integer
    OPR = 2    # arithmetic / relational / I-O operation
    LOD = 3    # load variable onto stack
    STO = 4    # pop stack top and store into variable
    CAL = 5    # call function
    RET = 6    # return from function
    ICT = 7    # increment stack top (allocate local-variable frame)
    JMP = 8    # unconditional jump
    JPC = 9    # jump if top-of-stack == 0  (conditional jump / "jump if false")


class Opr:
    """Sub-operation codes carried inside OPR instructions."""
    NEG  = 10;  ADD  = 11;  SUB  = 12;  MUL  = 13
    DIV  = 14;  ODD  = 15;  EQ   = 16;  LS   = 17
    GR   = 18;  NEQ  = 19;  LSEQ = 20;  GREQ = 21
    WRT  = 22   # write integer to stdout
    WRL  = 23   # write newline to stdout

    _NAMES = {
        10: "neg", 11: "add", 12: "sub", 13: "mul",
        14: "div", 15: "odd", 16: "eq",  17: "ls",
        18: "gr",  19: "neq", 20: "lseq",21: "greq",
        22: "wrt", 23: "wrl",
    }

    @classmethod
    def name(cls, code: int) -> str:
        """Return the mnemonic string for a sub-operation code."""
        return cls._NAMES.get(code, "?")


# ---------------------------------------------------------------------------
# Instruction representations
# ---------------------------------------------------------------------------

@dataclass
class InstLit:
    """Instruction with a single integer operand  (LIT, ICT, JMP, JPC)."""
    opcode: int = Op.LIT
    value:  int = 0


@dataclass
class InstAddr:
    """Instruction with a relative-address operand  (LOD, STO, CAL, RET)."""
    opcode:   int     = Op.LOD
    rel_addr: RelAddr = field(default_factory=lambda: RelAddr(0, 0))


@dataclass
class InstOpr:
    """Instruction encoding an arithmetic or relational operation  (OPR)."""
    opcode: int = Op.OPR
    opr:    int = 0   # one of the Opr.* constants


# ---------------------------------------------------------------------------
# Code generator
# ---------------------------------------------------------------------------

class CodeGen:
    """
    Translates the parse tree (implicitly) into a sequence of PL/0' instructions,
    and then executes those instructions on the built-in stack machine.
    """

    MAXCODE  = 200    # maximum number of instructions
    MAXMEM   = 2000   # runtime stack depth
    MAXREG   = 20     # extra headroom above the stack top (for expression operands)
    MAXLEVEL = 5      # maximum block nesting depth

    def __init__(self, source: GetSource, table: Table, trace: bool):
        """
        Parameters
        ----------
        source : used for fatal-error reporting.
        table  : symbol table (needed to look up relative addresses and param counts).
        trace  : if True, print the stack state and each instruction as it executes.
        """
        self._src   = source
        self._tbl   = table
        self._trace = trace
        self._code  = [None] * self.MAXCODE   # instruction array
        self._top   = -1                       # index of the last emitted instruction

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _advance(self) -> int:
        """
        Increment the instruction pointer and check for overflow.
        Returns the new (valid) instruction index.
        """
        self._top += 1
        if self._top < self.MAXCODE:
            return self._top
        self._src.error_fatal("too many code")

    # ------------------------------------------------------------------
    # Address queries
    # ------------------------------------------------------------------

    def next_addr(self) -> int:
        """Return the address that the *next* emitted instruction will occupy."""
        return self._top + 1

    # ------------------------------------------------------------------
    # Instruction emitters
    # ------------------------------------------------------------------

    def gen_lit(self, value: int) -> int:
        """Emit  LIT value  — push an integer literal.  Returns the instruction address."""
        i = self._advance()
        self._code[i] = InstLit(Op.LIT, value)
        return i

    def gen_opr(self, opr: int) -> int:
        """Emit  OPR opr  — arithmetic / relational / I-O operation."""
        i = self._advance()
        self._code[i] = InstOpr(Op.OPR, opr)
        return i

    def gen_lod(self, ti: int) -> int:
        """Emit  LOD level,addr  — load variable at symbol-table index *ti*."""
        i = self._advance()
        self._code[i] = InstAddr(Op.LOD, self._tbl.rel_addr_at(ti))
        return i

    def gen_sto(self, ti: int) -> int:
        """Emit  STO level,addr  — store into variable at symbol-table index *ti*."""
        i = self._advance()
        self._code[i] = InstAddr(Op.STO, self._tbl.rel_addr_at(ti))
        return i

    def gen_cal(self, ti: int) -> int:
        """Emit  CAL level,addr  — call function at symbol-table index *ti*."""
        i = self._advance()
        self._code[i] = InstAddr(Op.CAL, self._tbl.rel_addr_at(ti))
        return i

    def gen_ret(self) -> int:
        """
        Emit  RET level,num_params  — return from the current function.
        Suppresses a duplicate RET if one was just emitted.
        """
        if self._top >= 0 and self._code[self._top].opcode == Op.RET:
            return self._top   # avoid back-to-back RETs
        i = self._advance()
        self._code[i] = InstAddr(
            Op.RET,
            RelAddr(self._tbl.block_level(), self._tbl.func_params()),
        )
        return i

    def gen_ict(self, size: int) -> int:
        """Emit  ICT size  — allocate *size* slots for the current frame."""
        i = self._advance()
        self._code[i] = InstLit(Op.ICT, size)
        return i

    def gen_jmp(self, target: int) -> int:
        """Emit  JMP target  — unconditional jump.  Returns the instruction address."""
        i = self._advance()
        self._code[i] = InstLit(Op.JMP, target)
        return i

    def gen_jpc(self, target: int) -> int:
        """Emit  JPC target  — jump if stack top == 0.  Returns the instruction address."""
        i = self._advance()
        self._code[i] = InstLit(Op.JPC, target)
        return i

    def back_patch(self, addr: int) -> None:
        """
        Fill in the jump target of the instruction at *addr* with the
        address of the *next* instruction to be emitted.
        """
        self._code[addr].value = self._top + 1

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_code(self) -> None:
        """Print every generated instruction to stdout."""
        print("\n ******** code ********")
        for i in range(self._top + 1):
            print(f"{i}: {self._format(i)}")

    def _format(self, i: int) -> str:
        """Return a human-readable string for instruction at address *i*."""
        inst = self._code[i]
        op   = inst.opcode
        _NAMES = {
            Op.LIT: "lit", Op.OPR: "opr", Op.LOD: "lod", Op.STO: "sto",
            Op.CAL: "cal", Op.RET: "ret", Op.ICT: "ict", Op.JMP: "jmp",
            Op.JPC: "jpc",
        }
        mnemonic = _NAMES.get(op, "???")
        if isinstance(inst, InstLit):
            return f"{mnemonic},{inst.value}"
        if isinstance(inst, InstAddr):
            return f"{mnemonic},{inst.rel_addr.level},{inst.rel_addr.addr}"
        if isinstance(inst, InstOpr):
            return f"{mnemonic},{Opr.name(inst.opr)}"
        return mnemonic

    # ------------------------------------------------------------------
    # Virtual machine
    # ------------------------------------------------------------------

    def execute(self) -> None:
        """
        Run the generated instructions on the PL/0' stack machine.

        The machine halts when the return address of the main block
        (stored at stack[1] = 0) sets pc back to 0.
        """
        stack   = [0] * self.MAXMEM
        display = [0] * self.MAXLEVEL   # display[l] = frame base of block at level l

        # Initial state: pc = 0, top = 0.
        # Slots 0 and 1 in the main frame are pre-filled:
        #   stack[0] = saved display[0] (0 = no caller)
        #   stack[1] = return address   (0 = halt when returned to)
        pc  = 0
        top = 0
        stack[0]   = 0
        stack[1]   = 0
        display[0] = 0   # main block starts at stack base 0

        print("start execution")
        if self._trace:
            print("\n ******** trace ********")

        while True:
            inst = self._code[pc]

            if self._trace:
                top_disp = max(top - 1, 0)
                print(f"\tstack[{top_disp}] = {stack[top_disp]}")
                print(f"\t{pc}:", self._format(pc))

            pc += 1   # advance past the current instruction
                      # (jump/call/return instructions will overwrite pc)

            op = inst.opcode

            # ---- LIT: push literal ----------------------------------------
            if op == Op.LIT:
                stack[top] = inst.value
                top += 1

            # ---- LOD: load variable ----------------------------------------
            elif op == Op.LOD:
                ra = inst.rel_addr
                stack[top] = stack[display[ra.level] + ra.addr]
                top += 1

            # ---- STO: store variable ----------------------------------------
            elif op == Op.STO:
                ra = inst.rel_addr
                top -= 1
                stack[display[ra.level] + ra.addr] = stack[top]

            # ---- CAL: call function ----------------------------------------
            elif op == Op.CAL:
                ra  = inst.rel_addr
                lev = ra.level + 1   # callee's block level = name's level + 1
                # Set up the callee's frame header at current top of stack.
                stack[top]     = display[lev]   # save display entry for caller's level
                stack[top + 1] = pc             # save return address (already advanced)
                display[lev]   = top            # new frame base for callee
                pc = ra.addr                    # jump to function entry point

            # ---- RET: return from function ---------------------------------
            elif op == Op.RET:
                ra     = inst.rel_addr
                retval = stack[top - 1]           # return value is at top of stack
                base   = display[ra.level]        # frame base of the returning function
                display[ra.level] = stack[base]   # restore saved display entry
                pc  = stack[base + 1]             # restore return address
                top = base - ra.addr              # pop parameters (negative offsets)
                stack[top] = retval               # push return value
                top += 1

            # ---- ICT: allocate local variables ----------------------------
            elif op == Op.ICT:
                top += inst.value
                if top >= self.MAXMEM - self.MAXREG:
                    self._src.error_fatal("stack overflow")

            # ---- JMP: unconditional jump -----------------------------------
            elif op == Op.JMP:
                pc = inst.value

            # ---- JPC: conditional jump (jump if false = 0) -----------------
            elif op == Op.JPC:
                top -= 1
                if stack[top] == 0:
                    pc = inst.value

            # ---- OPR: arithmetic / relational / I-O ------------------------
            elif op == Op.OPR:
                opr = inst.opr
                if   opr == Opr.NEG:  stack[top - 1] = -stack[top - 1]
                elif opr == Opr.ADD:  top -= 1; stack[top - 1] += stack[top]
                elif opr == Opr.SUB:  top -= 1; stack[top - 1] -= stack[top]
                elif opr == Opr.MUL:  top -= 1; stack[top - 1] *= stack[top]
                elif opr == Opr.DIV:  top -= 1; stack[top - 1] //= stack[top]
                elif opr == Opr.ODD:  stack[top - 1] &= 1
                elif opr == Opr.EQ:   top -= 1; stack[top - 1] = int(stack[top - 1] == stack[top])
                elif opr == Opr.LS:   top -= 1; stack[top - 1] = int(stack[top - 1] <  stack[top])
                elif opr == Opr.GR:   top -= 1; stack[top - 1] = int(stack[top - 1] >  stack[top])
                elif opr == Opr.NEQ:  top -= 1; stack[top - 1] = int(stack[top - 1] != stack[top])
                elif opr == Opr.LSEQ: top -= 1; stack[top - 1] = int(stack[top - 1] <= stack[top])
                elif opr == Opr.GREQ: top -= 1; stack[top - 1] = int(stack[top - 1] >= stack[top])
                elif opr == Opr.WRT:  top -= 1; print(stack[top], end=" ")
                elif opr == Opr.WRL:  print()

            # ---- Halt when pc returns to 0 (main block returned) -----------
            if pc == 0:
                break
