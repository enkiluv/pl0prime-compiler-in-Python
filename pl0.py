#!/usr/bin/env python3
"""
PL/0' Compiler  —  Main Entry Point
=====================================
Usage
-----
    python pl0.py [options] <source.pl0>

Options
-------
    -s   Print the symbol table at the end of each block
    -o   List the generated object code after compilation
    -t   Trace execution (print stack state before each instruction)
    -b   Use the bytecode code generator (CodeGenB) instead of the default

Output
------
  A file  <source.pl0>.html  is always written alongside the source.
  It contains an HTML-annotated copy of the source with errors highlighted
  in colour:
    blue  = inserted (missing) token
    red   = deleted (spurious) token
    green = type error

Example
-------
    python pl0.py -o fact.pl0       # compile, list code, execute
    python pl0.py -sotb ex1.pl0     # all options enabled
"""

import sys
from get_source import GetSource
from table import Table
from code_gen import CodeGen
from code_gen_b import CodeGenB
from compiler import Compiler


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    # ---- Parse option flags ------------------------------------------------
    sym_table = False   # -s : print symbol table
    obj_code  = False   # -o : list object code
    trace     = False   # -t : trace execution
    bytecode  = False   # -b : use bytecode generator

    if args[0].startswith('-'):
        for ch in args[0][1:]:
            if   ch == 's': sym_table = True
            elif ch == 'o': obj_code  = True
            elif ch == 't': trace     = True
            elif ch == 'b': bytecode  = True
            else:
                print(f"Unknown option: -{ch}")
        args = args[1:]

    if not args:
        print("Error: no source file specified.")
        sys.exit(1)

    filename = args[0]

    # ---- Assemble the compiler pipeline -----------------------------------
    source  = GetSource()
    table   = Table(source, sym_table)

    # GetSource needs a back-reference to Table for a few error-recovery
    # operations (entering dummy symbols); set it here to break the cycle.
    source.set_table(table)   # (currently unused; reserved for future use)

    if bytecode:
        codegen = CodeGenB(source, table, trace)
    else:
        codegen = CodeGen(source, table, trace)

    compiler = Compiler(source, table, codegen, obj_code)

    # ---- Compile and (conditionally) execute ------------------------------
    if not source.open_source(filename):
        sys.exit(1)

    if compiler.compile():
        codegen.execute()

    source.close_source()


if __name__ == "__main__":
    main()
