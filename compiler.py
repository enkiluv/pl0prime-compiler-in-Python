"""
PL/0' Compiler  —  Recursive-Descent Parser / Compiler
=======================================================
This module drives the entire compilation process.  It reads tokens from
``GetSource``, manages the symbol table via ``Table``, and emits instructions
via ``CodeGen``.

PL/0' Grammar (simplified)
---------------------------
  program   = block '.'
  block     = { 'const' id '=' num {',' id '=' num} ';'
              | 'var'   id {',' id} ';'
              | 'function' id '(' [id {',' id}] ')' block ';'
              }
              statement
  statement = id ':=' expr
            | 'if' condition 'then' statement
            | 'return' expr
            | 'begin' statement {';' statement} 'end'
            | 'while' condition 'do' statement
            | 'write' expr
            | 'writeln'
            | ε
  condition = 'odd' expr
            | expr relop expr
  expr      = ['+' | '-'] term { ('+' | '-') term }
  term      = factor { ('*' | '/') factor }
  factor    = id | num | '(' expr ')' | id '(' [expr {',' expr}] ')'
  relop     = '=' | '<>' | '<' | '>' | '<=' | '>='
"""

from get_source import (
    GetSource, TokenKind,
    CONST_ID, VAR_ID, PAR_ID, FUNC_ID,
)
from table import Table
from code_gen import CodeGen, Opr


# Frame offset at which local variables start.
# Slots 0 and 1 are reserved (saved display entry and return address).
FIRST_ADDR = 2

# If the number of errors is below this threshold we still attempt to execute.
MIN_ERRORS = 3


class Compiler:
    """
    Translates a PL/0' source program into stack-machine instructions
    using a single-pass recursive-descent strategy.

    Invariant: ``self._tok`` always holds the lookahead token (the next
    token not yet consumed by the grammar).
    """

    def __init__(
        self,
        source: GetSource,
        table:  Table,
        codegen: CodeGen,
        list_code: bool,
    ):
        self._src  = source
        self._tbl  = table
        self._cg   = codegen
        self._list = list_code
        self._tok  = None   # current lookahead token

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def compile(self) -> bool:
        """
        Compile the source program.

        Returns True if the error count is below MIN_ERRORS, which means
        the generated code is safe to execute.
        """
        print("start compilation")
        self._src.init_source()
        self._tok = self._src.next_token()        # prime the lookahead
        self._tbl.block_begin(FIRST_ADDR)
        self._block(0)                             # 0 = no parent function
        self._src.final_source()

        n = self._src.error_count()
        if n:
            print(f"{n} errors")
        if self._list:
            self._cg.list_code()
        return n < MIN_ERRORS

    # ------------------------------------------------------------------
    # Block
    # ------------------------------------------------------------------

    def _block(self, func_idx: int) -> None:
        """
        Compile one block (the main program or a function body).

        *func_idx* is the symbol-table index of the enclosing function name;
        0 for the main block.

        Block structure in the generated code:
          JMP  →  (skips over nested function bodies)
          ... nested function code ...
          ICT  (allocate frame for local variables)
          ... body statement ...
          RET
        """
        # Emit a forward JMP over any nested function definitions.
        # Its target will be filled in by back_patch once we reach the body.
        jmp_addr = self._cg.gen_jmp(0)

        # Compile declarations (const / var / function)
        while True:
            k = self._tok.kind
            if k == TokenKind.CONST:
                self._tok = self._src.next_token()
                self._const_decl()
            elif k == TokenKind.VAR:
                self._tok = self._src.next_token()
                self._var_decl()
            elif k == TokenKind.FUNCTION:
                self._tok = self._src.next_token()
                self._func_decl()
            else:
                break   # no more declarations

        # Back-patch the opening JMP to the start of the body
        self._cg.back_patch(jmp_addr)

        # Fix the function's code-start address in the symbol table
        if func_idx != 0:
            self._tbl.fix_func_addr(func_idx, self._cg.next_addr())

        # Allocate frame space for all local variables declared above
        self._cg.gen_ict(self._tbl.frame_size())

        # Compile the body
        self._stmt()

        # Return to caller
        self._cg.gen_ret()

        # Signal to the symbol table that this block has ended
        self._tbl.block_end()

    # ------------------------------------------------------------------
    # Declarations
    # ------------------------------------------------------------------

    def _const_decl(self) -> None:
        """
        Compile:  id '=' num { ',' id '=' num } ';'
        (the leading 'const' keyword has already been consumed)
        """
        while True:
            if self._tok.kind == TokenKind.IDENT:
                self._src.set_id_kind(CONST_ID)
                name_tok = self._tok
                # Expect  '='  after the name
                self._tok = self._src.check_get(
                    self._src.next_token(), TokenKind.EQUAL)
                if self._tok.kind == TokenKind.NUMBER:
                    self._tbl.enter_const(name_tok.name, self._tok.value)
                else:
                    self._src.error_type("number")
                self._tok = self._src.next_token()
            else:
                self._src.error_missing_id()

            # Continue if followed by ','
            if self._tok.kind != TokenKind.COMMA:
                if self._tok.kind == TokenKind.IDENT:
                    # Adjacent identifier — assume missing comma
                    self._src.error_insert(TokenKind.COMMA)
                    continue
                break
            self._tok = self._src.next_token()

        self._tok = self._src.check_get(self._tok, TokenKind.SEMICOLON)

    def _var_decl(self) -> None:
        """
        Compile:  id { ',' id } ';'
        (the leading 'var' keyword has already been consumed)
        """
        while True:
            if self._tok.kind == TokenKind.IDENT:
                self._src.set_id_kind(VAR_ID)
                self._tbl.enter_var(self._tok.name)
                self._tok = self._src.next_token()
            else:
                self._src.error_missing_id()

            if self._tok.kind != TokenKind.COMMA:
                if self._tok.kind == TokenKind.IDENT:
                    self._src.error_insert(TokenKind.COMMA)
                    continue
                break
            self._tok = self._src.next_token()

        self._tok = self._src.check_get(self._tok, TokenKind.SEMICOLON)

    def _func_decl(self) -> None:
        """
        Compile:  id '(' [id {',' id}] ')' block ';'
        (the leading 'function' keyword has already been consumed)
        """
        if self._tok.kind != TokenKind.IDENT:
            self._src.error_missing_id()
            return

        self._src.set_id_kind(FUNC_ID)

        # Register the function name; its entry address is tentative for now
        # (it will be fixed by fix_func_addr inside _block).
        fi = self._tbl.enter_func(self._tok.name, self._cg.next_addr())

        self._tok = self._src.check_get(
            self._src.next_token(), TokenKind.LPAREN)

        # Parameters share the function's own block level
        self._tbl.block_begin(FIRST_ADDR)

        # Parse the parameter list
        while True:
            if self._tok.kind == TokenKind.IDENT:
                self._src.set_id_kind(PAR_ID)
                self._tbl.enter_param(self._tok.name)
                self._tok = self._src.next_token()
            else:
                break
            if self._tok.kind != TokenKind.COMMA:
                if self._tok.kind == TokenKind.IDENT:
                    self._src.error_insert(TokenKind.COMMA)
                    continue
                break
            self._tok = self._src.next_token()

        self._tok = self._src.check_get(self._tok, TokenKind.RPAREN)
        self._tbl.end_params()   # fix parameter frame offsets

        # Tolerate a stray ';' between ')' and the block
        if self._tok.kind == TokenKind.SEMICOLON:
            self._src.error_delete()
            self._tok = self._src.next_token()

        self._block(fi)
        self._tok = self._src.check_get(self._tok, TokenKind.SEMICOLON)

    # ------------------------------------------------------------------
    # Statements
    # ------------------------------------------------------------------

    # Token kinds that can legally begin a statement.
    # Used for error recovery inside begin...end blocks.
    _STMT_STARTERS = frozenset({
        TokenKind.IF, TokenKind.BEGIN, TokenKind.RETURN,
        TokenKind.WHILE, TokenKind.WRITE, TokenKind.WRITELN,
    })

    def _stmt(self) -> None:
        """
        Compile a single statement.

        The outer ``while True`` loop is used only for error recovery:
        when an unexpected token is encountered it is discarded and
        the loop tries again.
        """
        while True:
            k = self._tok.kind

            # ---- Assignment:  id ':=' expr --------------------------------
            if k == TokenKind.IDENT:
                ti = self._tbl.search(self._tok.name, VAR_ID)
                ik = self._tbl.kind_at(ti)
                self._src.set_id_kind(ik)
                if ik != VAR_ID and ik != PAR_ID:
                    self._src.error_type("var/par")
                self._tok = self._src.check_get(
                    self._src.next_token(), TokenKind.ASSIGN)
                self._expr()
                self._cg.gen_sto(ti)
                return

            # ---- if condition then statement --------------------------------
            elif k == TokenKind.IF:
                self._tok = self._src.next_token()
                self._cond()
                self._tok = self._src.check_get(self._tok, TokenKind.THEN)
                jpc_addr = self._cg.gen_jpc(0)
                self._stmt()
                self._cg.back_patch(jpc_addr)
                return

            # ---- return expr -----------------------------------------------
            elif k == TokenKind.RETURN:
                self._tok = self._src.next_token()
                self._expr()
                self._cg.gen_ret()
                return

            # ---- begin statement { ';' statement } end --------------------
            elif k == TokenKind.BEGIN:
                self._tok = self._src.next_token()
                while True:
                    self._stmt()
                    # Expect ';' between statements or 'end' to close the block
                    while True:
                        if self._tok.kind == TokenKind.SEMICOLON:
                            self._tok = self._src.next_token()
                            break   # continue outer loop (next statement)
                        if self._tok.kind == TokenKind.END:
                            self._tok = self._src.next_token()
                            return  # block complete
                        if self._tok.kind in self._STMT_STARTERS:
                            # Missing semicolon — insert it and parse next stmt
                            self._src.error_insert(TokenKind.SEMICOLON)
                            break
                        # Unexpected token — discard and keep looking
                        self._src.error_delete()
                        self._tok = self._src.next_token()

            # ---- while condition do statement ------------------------------
            elif k == TokenKind.WHILE:
                self._tok = self._src.next_token()
                loop_top = self._cg.next_addr()   # target for the back-edge
                self._cond()
                self._tok = self._src.check_get(self._tok, TokenKind.DO)
                jpc_addr = self._cg.gen_jpc(0)   # exit jump (patched below)
                self._stmt()
                self._cg.gen_jmp(loop_top)        # back to condition
                self._cg.back_patch(jpc_addr)
                return

            # ---- write expr -----------------------------------------------
            elif k == TokenKind.WRITE:
                self._tok = self._src.next_token()
                self._expr()
                self._cg.gen_opr(Opr.WRT)
                return

            # ---- writeln --------------------------------------------------
            elif k == TokenKind.WRITELN:
                self._tok = self._src.next_token()
                self._cg.gen_opr(Opr.WRL)
                return

            # ---- Empty statement  (';'  or  'end'  as lookahead) ----------
            elif k in (TokenKind.END, TokenKind.SEMICOLON):
                return

            # ---- Error recovery: discard the unexpected token -------------
            else:
                self._src.error_delete()
                self._tok = self._src.next_token()
                # continue the outer while loop and try again

    # ------------------------------------------------------------------
    # Expressions
    # ------------------------------------------------------------------

    def _expr(self) -> None:
        """
        Compile:  ['+' | '-'] term { ('+' | '-') term }

        Handles the optional leading sign and accumulates addition/subtraction.
        """
        k = self._tok.kind
        if k in (TokenKind.PLUS, TokenKind.MINUS):
            self._tok = self._src.next_token()
            self._term()
            if k == TokenKind.MINUS:
                self._cg.gen_opr(Opr.NEG)   # unary minus
        else:
            self._term()

        k = self._tok.kind
        while k in (TokenKind.PLUS, TokenKind.MINUS):
            self._tok = self._src.next_token()
            self._term()
            self._cg.gen_opr(Opr.ADD if k == TokenKind.PLUS else Opr.SUB)
            k = self._tok.kind

    def _term(self) -> None:
        """Compile:  factor { ('*' | '/') factor }"""
        self._factor()
        k = self._tok.kind
        while k in (TokenKind.MULT, TokenKind.DIV):
            self._tok = self._src.next_token()
            self._factor()
            self._cg.gen_opr(Opr.MUL if k == TokenKind.MULT else Opr.DIV)
            k = self._tok.kind

    def _factor(self) -> None:
        """
        Compile a single factor:
          - variable / parameter reference  →  LOD
          - named constant                  →  LIT
          - function call                   →  (push args) CAL
          - integer literal                 →  LIT
          - '(' expr ')'                    →  nested expression
        """
        k = self._tok.kind

        if k == TokenKind.IDENT:
            ti = self._tbl.search(self._tok.name, VAR_ID)
            ik = self._tbl.kind_at(ti)
            self._src.set_id_kind(ik)

            if ik == VAR_ID or ik == PAR_ID:
                # Variable or parameter: load its value
                self._cg.gen_lod(ti)
                self._tok = self._src.next_token()

            elif ik == CONST_ID:
                # Named constant: push its value as a literal
                self._cg.gen_lit(self._tbl.value_at(ti))
                self._tok = self._src.next_token()

            elif ik == FUNC_ID:
                # Function call: push arguments, then emit CAL
                self._tok = self._src.next_token()
                if self._tok.kind == TokenKind.LPAREN:
                    num_args = 0
                    self._tok = self._src.next_token()
                    if self._tok.kind != TokenKind.RPAREN:
                        while True:
                            self._expr()
                            num_args += 1
                            if self._tok.kind == TokenKind.COMMA:
                                self._tok = self._src.next_token()
                                continue
                            self._tok = self._src.check_get(
                                self._tok, TokenKind.RPAREN)
                            break
                    else:
                        self._tok = self._src.next_token()

                    if self._tbl.params_at(ti) != num_args:
                        self._src.error_message("\\#par")   # wrong argument count
                else:
                    # Missing parentheses
                    self._src.error_insert(TokenKind.LPAREN)
                    self._src.error_insert(TokenKind.RPAREN)
                self._cg.gen_cal(ti)

        elif k == TokenKind.NUMBER:
            self._cg.gen_lit(self._tok.value)
            self._tok = self._src.next_token()

        elif k == TokenKind.LPAREN:
            self._tok = self._src.next_token()
            self._expr()
            self._tok = self._src.check_get(self._tok, TokenKind.RPAREN)

        # Error: a factor immediately followed by another factor (missing operator)
        if self._tok.kind in (TokenKind.IDENT, TokenKind.NUMBER, TokenKind.LPAREN):
            self._src.error_missing_op()
            self._factor()   # attempt to continue

    # ------------------------------------------------------------------
    # Conditions
    # ------------------------------------------------------------------

    # Maps relational-operator tokens to their OPR sub-operation codes
    _REL_OP: dict = {
        TokenKind.EQUAL:      Opr.EQ,
        TokenKind.LESS:       Opr.LS,
        TokenKind.GREATER:    Opr.GR,
        TokenKind.NOT_EQ:     Opr.NEQ,
        TokenKind.LESS_EQ:    Opr.LSEQ,
        TokenKind.GREATER_EQ: Opr.GREQ,
    }

    def _cond(self) -> None:
        """
        Compile a condition:
          'odd' expr       — true if the expression is odd
          expr relop expr  — standard relational comparison
        """
        if self._tok.kind == TokenKind.ODD:
            self._tok = self._src.next_token()
            self._expr()
            self._cg.gen_opr(Opr.ODD)
        else:
            self._expr()
            rel_tok = self._tok.kind
            if rel_tok not in self._REL_OP:
                self._src.error_type("rel-op")
            self._tok = self._src.next_token()
            self._expr()
            opr = self._REL_OP.get(rel_tok)
            if opr is not None:
                self._cg.gen_opr(opr)
