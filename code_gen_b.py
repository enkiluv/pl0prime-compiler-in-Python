"""
PL/0' Compiler  —  Bytecode Code Generator  (compact binary encoding)
======================================================================
``CodeGenB`` extends ``CodeGen`` to produce a compact *bytecode* array
(a Python ``bytearray``) instead of the object-level instruction array used
by the base class.  The VM in this class decodes and executes the bytes
directly, making the representation much closer to a real bytecode engine.

Encoding summary
----------------
  Instruction  │ Bytes
  ─────────────┼───────────────────────────────────────────────
  lit          │ opcode(1) + value(4, little-endian int32)
  lod/sto/cal  │ opcode(1) + level(1) + offset(2, little-endian int16)
  ret          │ opcode(1) + level(1) + num_params(1)
  ict/jmp/jpc  │ opcode(1) + target(2, little-endian int16)
  OPR ops      │ opcode(1)   [NEG, ADD, … WRL use the Opr.* values directly]
"""

import struct
from get_source import GetSource
from table import Table
from code_gen import CodeGen, Op, Opr


class CodeGenB(CodeGen):
    """
    Bytecode variant of the code generator.

    The ``bcode`` bytearray accumulates raw bytes during compilation;
    ``execute()`` runs those bytes on a stack machine without ever
    building higher-level instruction objects.
    """

    # Bytecode array is three times larger than the instruction-object array
    # because some instructions expand to up to 5 bytes.
    _MAXBCODE = CodeGen.MAXCODE * 3

    def __init__(self, source: GetSource, table: Table, trace: bool):
        super().__init__(source, table, trace)
        self.bcode      = bytearray(self._MAXBCODE)
        self._bi        = 0    # next free byte index in bcode
        self._prev_bi   = 0    # byte index of the most recently started instruction

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_space(self) -> None:
        """Ensure there is room for at least 5 more bytes."""
        self._prev_bi = self._bi
        if self._bi >= self._MAXBCODE - 5:
            self._src.error_fatal("too many code")
        # Also bump the base-class _top so next_addr() and back_patch() work.
        self._top += 1

    def _write_int16(self, v: int) -> None:
        """Append a signed 16-bit little-endian integer to bcode."""
        data = struct.pack('<h', v)
        self.bcode[self._bi]     = data[0]
        self.bcode[self._bi + 1] = data[1]
        self._bi += 2

    def _write_int32(self, v: int) -> None:
        """Append a signed 32-bit little-endian integer to bcode."""
        data = struct.pack('<i', v)
        for k in range(4):
            self.bcode[self._bi + k] = data[k]
        self._bi += 4

    @staticmethod
    def _read_int16(bcode: bytearray, pos: int) -> int:
        """Decode a signed 16-bit little-endian integer at *pos*."""
        return struct.unpack_from('<h', bcode, pos)[0]

    @staticmethod
    def _read_int32(bcode: bytearray, pos: int) -> int:
        """Decode a signed 32-bit little-endian integer at *pos*."""
        return struct.unpack_from('<i', bcode, pos)[0]

    # ------------------------------------------------------------------
    # Instruction emitters  (override base class)
    # ------------------------------------------------------------------

    def gen_lit(self, value: int) -> int:
        """LIT: opcode(1) + value(4)  →  5 bytes total."""
        self._check_space()
        addr = self._bi
        self.bcode[self._bi] = Op.LIT
        self._bi += 1
        self._write_int32(value)
        return addr

    def gen_opr(self, opr: int) -> int:
        """OPR sub-operation: 1 byte  (the Opr.* constant itself)."""
        self._check_space()
        addr = self._bi
        self.bcode[self._bi] = opr
        self._bi += 1
        return addr

    def gen_lod(self, ti: int) -> int:
        """LOD: opcode(1) + level(1) + offset(2)  →  4 bytes."""
        self._check_space()
        ra   = self._tbl.rel_addr_at(ti)
        addr = self._bi
        self.bcode[self._bi]     = Op.LOD
        self.bcode[self._bi + 1] = ra.level & 0xFF
        self._bi += 2
        self._write_int16(ra.addr)
        return addr

    def gen_sto(self, ti: int) -> int:
        """STO: opcode(1) + level(1) + offset(2)  →  4 bytes."""
        self._check_space()
        ra   = self._tbl.rel_addr_at(ti)
        addr = self._bi
        self.bcode[self._bi]     = Op.STO
        self.bcode[self._bi + 1] = ra.level & 0xFF
        self._bi += 2
        self._write_int16(ra.addr)
        return addr

    def gen_cal(self, ti: int) -> int:
        """CAL: opcode(1) + level(1) + entry-addr(2)  →  4 bytes."""
        self._check_space()
        ra   = self._tbl.rel_addr_at(ti)
        addr = self._bi
        self.bcode[self._bi]     = Op.CAL
        self.bcode[self._bi + 1] = ra.level & 0xFF
        self._bi += 2
        self._write_int16(ra.addr)
        return addr

    def gen_ret(self) -> int:
        """RET: opcode(1) + level(1) + num_params(1)  →  3 bytes."""
        # Suppress duplicate RET
        if self.bcode[self._prev_bi] == Op.RET:
            return self._bi - 3
        self._check_space()
        addr = self._bi
        self.bcode[self._bi]     = Op.RET
        self.bcode[self._bi + 1] = self._tbl.block_level() & 0xFF
        self.bcode[self._bi + 2] = self._tbl.func_params() & 0xFF
        self._bi += 3
        return addr

    def gen_ict(self, size: int) -> int:
        """ICT: opcode(1) + size(2)  →  3 bytes."""
        self._check_space()
        addr = self._bi
        self.bcode[self._bi] = Op.ICT
        self._bi += 1
        self._write_int16(size)
        return addr

    def gen_jmp(self, target: int) -> int:
        """JMP: opcode(1) + target(2)  →  3 bytes.  Returns byte address."""
        self._check_space()
        addr = self._bi
        self.bcode[self._bi] = Op.JMP
        self._bi += 1
        self._write_int16(target)
        return addr

    def gen_jpc(self, target: int) -> int:
        """JPC: opcode(1) + target(2)  →  3 bytes.  Returns byte address."""
        self._check_space()
        addr = self._bi
        self.bcode[self._bi] = Op.JPC
        self._bi += 1
        self._write_int16(target)
        return addr

    # ------------------------------------------------------------------
    # Back-patching
    # ------------------------------------------------------------------

    def next_addr(self) -> int:
        """Return the current byte offset (= address of the next instruction)."""
        return self._bi

    def back_patch(self, addr: int) -> None:
        """
        Fill in the jump target at byte address *addr + 1* with the
        current byte offset.  (addr+0 is the opcode; addr+1..2 are the target.)
        """
        data = struct.pack('<h', self._bi)
        self.bcode[addr + 1] = data[0]
        self.bcode[addr + 2] = data[1]

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_code(self) -> None:
        """Disassemble and print all generated bytecode to stdout."""
        print("\n ******** byte code ********")
        pos = 0
        while pos < self._bi:
            print(f"{pos}: ", end="")
            pos = self._print_bcode(pos)

    def _print_bcode(self, pos: int) -> int:
        """Disassemble one instruction starting at byte *pos*.  Returns next pos."""
        op = self.bcode[pos]
        _OP_NAMES = {
            Op.LIT: "lit", Op.LOD: "lod", Op.STO: "sto",
            Op.CAL: "cal", Op.RET: "ret", Op.ICT: "ict",
            Op.JMP: "jmp", Op.JPC: "jpc",
        }
        if op in _OP_NAMES:
            print(_OP_NAMES[op], end="")
            if op == Op.LIT:
                v = self._read_int32(self.bcode, pos + 1)
                print(f",{v}")
                return pos + 5
            if op in (Op.LOD, Op.STO, Op.CAL):
                level = self.bcode[pos + 1]
                addr  = self._read_int16(self.bcode, pos + 2)
                print(f",{level},{addr}")
                return pos + 4
            if op == Op.RET:
                level  = self.bcode[pos + 1]
                params = self.bcode[pos + 2]
                print(f",{level},{params}")
                return pos + 3
            # ICT, JMP, JPC
            target = self._read_int16(self.bcode, pos + 1)
            print(f",{target}")
            return pos + 3

        # OPR instructions are stored as a single byte using Opr.* values
        print(Opr.name(op))
        return pos + 1

    # ------------------------------------------------------------------
    # Virtual machine  (bytecode interpreter)
    # ------------------------------------------------------------------

    def execute(self) -> None:
        """Run the bytecode on the PL/0' stack machine."""
        stack   = [0] * self.MAXMEM
        display = [0] * self.MAXLEVEL

        pc  = 0
        top = 0
        stack[0]   = 0   # saved display[0] for main block
        stack[1]   = 0   # return address = 0  (halt condition)
        display[0] = 0

        print("start execution")
        if self._trace:
            print("\n ******** trace ********")

        while True:
            if self._trace:
                top_disp = max(top - 1, 0)
                print(f"\t\tstack[{top_disp}] = {stack[top_disp]}")
                print(f"\t{pc}:", end=" ")
                self._print_bcode(pc)

            op = self.bcode[pc]
            pc += 1

            if op == Op.LIT:
                stack[top] = self._read_int32(self.bcode, pc)
                pc  += 4
                top += 1

            elif op == Op.LOD:
                level = self.bcode[pc]
                addr  = self._read_int16(self.bcode, pc + 1)
                stack[top] = stack[display[level] + addr]
                pc  += 3
                top += 1

            elif op == Op.STO:
                level = self.bcode[pc]
                addr  = self._read_int16(self.bcode, pc + 1)
                top  -= 1
                stack[display[level] + addr] = stack[top]
                pc   += 3

            elif op == Op.CAL:
                level = self.bcode[pc]
                entry = self._read_int16(self.bcode, pc + 1)
                lev   = level + 1
                stack[top]     = display[lev]    # save display entry
                stack[top + 1] = pc + 3          # return address (after 3 operand bytes)
                display[lev]   = top
                pc = entry

            elif op == Op.RET:
                level  = self.bcode[pc]
                params = self.bcode[pc + 1]
                retval = stack[top - 1]
                base   = display[level]
                display[level] = stack[base]
                pc  = stack[base + 1]
                top = base - params
                stack[top] = retval
                top += 1

            elif op == Op.ICT:
                size = self._read_int16(self.bcode, pc)
                pc  += 2
                top += size
                if top >= self.MAXMEM - self.MAXREG:
                    self._src.error_fatal("stack overflow")

            elif op == Op.JMP:
                pc = self._read_int16(self.bcode, pc)

            elif op == Op.JPC:
                target = self._read_int16(self.bcode, pc)
                top   -= 1
                if stack[top] == 0:
                    pc = target
                else:
                    pc += 2

            else:
                # OPR instructions are stored as a single-byte opcode
                opr = op
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

            if pc == 0:
                break
