"""
PL/0' Compiler  —  Lexical Analyzer (Lexer)
============================================
Responsibilities:
  - Read source characters one at a time
  - Group characters into tokens
  - Report lexical and syntax errors
  - Write an HTML-annotated copy of the source (errors in colour)
"""

import sys
from enum import IntEnum


# ---------------------------------------------------------------------------
# Token kind constants
# ---------------------------------------------------------------------------

class TokenKind(IntEnum):
    """Every possible kind of token that the PL/0' lexer can produce."""

    # ---- Reserved keywords -------------------------------------------------
    BEGIN    = 0
    END      = 1
    IF       = 2
    THEN     = 3
    WHILE    = 4
    DO       = 5
    RETURN   = 6
    FUNCTION = 7
    VAR      = 8
    CONST    = 9
    ODD      = 10
    WRITE    = 11
    WRITELN  = 12
    END_OF_KEYWORD = 13    # sentinel: keyword range is [0, END_OF_KEYWORD)

    # ---- Operators and delimiters ------------------------------------------
    PLUS       = 14
    MINUS      = 15
    MULT       = 16
    DIV        = 17
    LPAREN     = 18
    RPAREN     = 19
    EQUAL      = 20
    LESS       = 21
    GREATER    = 22
    NOT_EQ     = 23
    LESS_EQ    = 24
    GREATER_EQ = 25
    COMMA      = 26
    PERIOD     = 27
    SEMICOLON  = 28
    ASSIGN     = 29
    END_OF_KEYSYM = 30     # sentinel: operator/delimiter range is [END_OF_KEYWORD, END_OF_KEYSYM)

    # ---- Other token types -------------------------------------------------
    IDENT  = 31   # user-defined identifier
    NUMBER = 32   # integer literal
    NUL    = 33   # placeholder / error


# Identifier-kind values (also used as HTML-coloring hints)
CONST_ID = 1
VAR_ID   = 2
PAR_ID   = 3
FUNC_ID  = 4


# ---------------------------------------------------------------------------
# Keyword / symbol string tables
# ---------------------------------------------------------------------------

# Maps every keyword and operator/delimiter TokenKind to its source spelling.
KEYWORD_STR: dict = {
    TokenKind.BEGIN:       "begin",
    TokenKind.END:         "end",
    TokenKind.IF:          "if",
    TokenKind.THEN:        "then",
    TokenKind.WHILE:       "while",
    TokenKind.DO:          "do",
    TokenKind.RETURN:      "return",
    TokenKind.FUNCTION:    "function",
    TokenKind.VAR:         "var",
    TokenKind.CONST:       "const",
    TokenKind.ODD:         "odd",
    TokenKind.WRITE:       "write",
    TokenKind.WRITELN:     "writeln",
    TokenKind.PLUS:        "+",
    TokenKind.MINUS:       "-",
    TokenKind.MULT:        "*",
    TokenKind.DIV:         "/",
    TokenKind.LPAREN:      "(",
    TokenKind.RPAREN:      ")",
    TokenKind.EQUAL:       "=",
    TokenKind.LESS:        "<",
    TokenKind.GREATER:     ">",
    TokenKind.NOT_EQ:      "<>",
    TokenKind.LESS_EQ:     "<=",
    TokenKind.GREATER_EQ:  ">=",
    TokenKind.COMMA:       ",",
    TokenKind.PERIOD:      ".",
    TokenKind.SEMICOLON:   ";",
    TokenKind.ASSIGN:      ":=",
}

# Reverse lookup: keyword spelling → TokenKind (keywords only, not symbols)
_KEYWORD_LOOKUP: dict = {
    s: k for k, s in KEYWORD_STR.items()
    if k < TokenKind.END_OF_KEYWORD
}


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

class Token:
    """Represents a single lexical token produced by the lexer."""

    __slots__ = ('kind', 'name', 'value')

    def __init__(self):
        self.kind:  TokenKind = TokenKind.NUL
        self.name:  str       = ""   # identifier spelling  (kind == IDENT)
        self.value: int       = 0    # integer literal value (kind == NUMBER)

    def __str__(self) -> str:
        if self.kind < TokenKind.END_OF_KEYSYM:
            return KEYWORD_STR.get(self.kind, "?")
        if self.kind == TokenKind.IDENT:
            return self.name
        return str(self.value)


# ---------------------------------------------------------------------------
# GetSource
# ---------------------------------------------------------------------------

class GetSource:
    """
    Source-file manager: reads characters, produces tokens, reports errors,
    and writes an HTML-annotated copy of the source for error visualisation.

    Typical call sequence
    ---------------------
    src = GetSource()
    src.open_source("prog.pl0")
    src.init_source()           # write HTML header, reset state
    tok = src.next_token()      # first token
    ...                         # compilation loop
    src.final_source()          # write HTML footer
    src.close_source()
    """

    MAXERROR = 30    # maximum errors before forced abort
    MAXNUM   = 14    # maximum digits in an integer literal
    MAXNAME  = 31    # maximum length of an identifier
    TAB      = 5     # visual width of a tab character

    # HTML colours for error annotations
    _INSERT_COLOR = "#0000FF"
    _DELETE_COLOR = "#FF0000"
    _TYPE_COLOR   = "#00FF00"

    def __init__(self):
        self._src       = None    # source file handle
        self._html      = None    # HTML output file handle
        self._line      = ""      # current input line (without trailing newline)
        self._line_pos  = -1      # next character position in _line; -1 = need new line
        self._ch        = '\n'    # last character read from source
        self.token      = Token() # most recently produced token
        self.id_kind    = VAR_ID  # kind of current identifier (for HTML coloring)
        self._spaces    = 0       # whitespace count before current token
        self._newlines  = 0       # newline count before current token
        self._printed   = True    # True  → current token has already been written to HTML
        self._error_cnt = 0       # number of errors reported so far
        self._char_cls  = self._build_char_class()
        self._table     = None   # optional back-reference to Table

    def set_table(self, table) -> None:
        """Store an optional back-reference to the Table (reserved for future use)."""
        self._table = table

    # ------------------------------------------------------------------
    # Character-class table
    # ------------------------------------------------------------------

    def _build_char_class(self) -> list:
        """
        Build a 256-entry array mapping ord(ch) to a character class.

        Most characters map to their corresponding single-character TokenKind
        (e.g. '+' → PLUS).  Letters and digits get special CharClass values;
        ':' gets a dedicated class because it starts the ':=' digraph.
        Anything else is OTHERS (= TokenKind.NUL treated as unknown).
        """
        LETTER = 35
        DIGIT  = 36
        COLON  = 37
        OTHERS = int(TokenKind.NUL)

        cc = [OTHERS] * 256

        for c in range(ord('0'), ord('9') + 1): cc[c] = DIGIT
        for c in range(ord('A'), ord('Z') + 1): cc[c] = LETTER
        for c in range(ord('a'), ord('z') + 1): cc[c] = LETTER

        for ch, kind in [
            ('+', TokenKind.PLUS),    ('-', TokenKind.MINUS),
            ('*', TokenKind.MULT),    ('/', TokenKind.DIV),
            ('(', TokenKind.LPAREN),  (')', TokenKind.RPAREN),
            ('=', TokenKind.EQUAL),   ('<', TokenKind.LESS),
            ('>', TokenKind.GREATER), (',', TokenKind.COMMA),
            ('.', TokenKind.PERIOD),  (';', TokenKind.SEMICOLON),
        ]:
            cc[ord(ch)] = int(kind)

        cc[ord(':')] = COLON

        # Store the special class values so next_token() can test for them.
        self._LETTER = LETTER
        self._DIGIT  = DIGIT
        self._COLON  = COLON
        return cc

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

    def open_source(self, filename: str) -> bool:
        """Open the PL/0 source file and create the companion HTML output file."""
        try:
            self._src  = open(filename, 'r', encoding='utf-8-sig')
            self._html = open(filename + '.html', 'w', encoding='utf-8')
            return True
        except OSError:
            print(f"Cannot open: {filename}")
            return False

    def close_source(self) -> None:
        """Close both file handles."""
        for f in (self._src, self._html):
            if f:
                try:
                    f.close()
                except OSError:
                    pass

    def init_source(self) -> None:
        """Reset lexer state and write the HTML file header."""
        self._line_pos = -1
        self._printed  = True
        self._html.write(
            "<HTML>\n<HEAD>\n"
            "<TITLE>compiled source program</TITLE>\n"
            "</HEAD>\n<BODY>\n<PRE>\n"
        )
        # Read the first real character now so that the synthetic '\n' used
        # during __init__ is never counted as a blank line in the HTML output.
        self._newlines = 0
        self._spaces   = 0
        self._ch       = self._next_char()

    def final_source(self) -> None:
        """
        Flush the last token to HTML (inserting '.' if missing) and
        write the HTML file footer.
        """
        if self.token.kind == TokenKind.PERIOD:
            self._flush_token()
        else:
            self.error_insert(TokenKind.PERIOD)
        self._html.write("\n</PRE>\n</BODY>\n</HTML>\n")

    # ------------------------------------------------------------------
    # Error reporting
    # ------------------------------------------------------------------

    def _bump_error(self) -> None:
        """Increment error counter; abort compilation if the limit is reached."""
        self._error_cnt += 1
        if self._error_cnt > self.MAXERROR:
            self._html.write("too many errors\n</PRE>\n</BODY>\n</HTML>\n")
            print("abort compilation")
            sys.exit(1)

    def error_type(self, msg: str) -> None:
        """
        Report a type error at the current token position.
        The offending token is coloured in the HTML output.
        """
        self._flush_spaces()
        self._html.write(f'<FONT COLOR={self._TYPE_COLOR}>{msg}</FONT>')
        self._flush_token()
        self._bump_error()

    def error_insert(self, kind: TokenKind) -> None:
        """Mark a missing token of the given *kind* as inserted in the HTML output."""
        self._flush_spaces()   # flush pending whitespace so the marker appears in-place
        kw = KEYWORD_STR.get(kind, "?")
        self._html.write(f'<FONT COLOR={self._INSERT_COLOR}><b>{kw}</b></FONT>')
        self._bump_error()

    def error_missing_id(self) -> None:
        """Mark a missing identifier."""
        self._html.write(f'<FONT COLOR={self._INSERT_COLOR}>Id</FONT>')
        self._bump_error()

    def error_missing_op(self) -> None:
        """Mark a missing operator (shown as '@' in the HTML output)."""
        self._html.write(f'<FONT COLOR={self._INSERT_COLOR}>@</FONT>')
        self._bump_error()

    def error_delete(self) -> None:
        """
        Mark the current token as spurious (to be deleted).
        Renders the token in red in the HTML output.
        """
        k = self.token.kind
        self._flush_spaces()
        self._printed = True   # prevent double-printing
        if k < TokenKind.END_OF_KEYWORD:
            self._html.write(
                f'<FONT COLOR={self._DELETE_COLOR}><b>{KEYWORD_STR[k]}</b></FONT>')
        elif k < TokenKind.END_OF_KEYSYM:
            self._html.write(
                f'<FONT COLOR={self._DELETE_COLOR}>{KEYWORD_STR[k]}</FONT>')
        elif k == TokenKind.IDENT:
            self._html.write(
                f'<FONT COLOR={self._DELETE_COLOR}>{self.token.name}</FONT>')
        elif k == TokenKind.NUMBER:
            self._html.write(
                f'<FONT COLOR={self._DELETE_COLOR}>{self.token.value}</FONT>')

    def error_message(self, msg: str) -> None:
        """Write a generic error message into the HTML output."""
        self._html.write(f'<FONT COLOR={self._TYPE_COLOR}>{msg}</FONT>')
        self._bump_error()

    def error_fatal(self, msg: str) -> None:
        """
        Report a fatal, unrecoverable error.
        Writes the message to HTML and terminates the process.
        """
        self.error_message(msg)
        self._html.write("fatal errors\n</PRE>\n</BODY>\n</HTML>\n")
        if self._error_cnt > 0:
            print(f"total {self._error_cnt} errors")
        print("abort compilation")
        sys.exit(1)

    def error_count(self) -> int:
        """Return the total number of errors reported so far."""
        return self._error_cnt

    # ------------------------------------------------------------------
    # Low-level character I/O
    # ------------------------------------------------------------------

    def _next_char(self) -> str:
        """Read and return the next character from the source file."""
        if self._line_pos == -1:
            line = self._src.readline()
            if line == "":
                self.error_fatal("unexpected end of file")
            self._line    = line.rstrip('\r\n')
            self._line_pos = 0
        if self._line_pos >= len(self._line):
            self._line_pos = -1
            return '\n'
        ch = self._line[self._line_pos]
        self._line_pos += 1
        return ch

    # ------------------------------------------------------------------
    # Token scanning
    # ------------------------------------------------------------------

    def next_token(self) -> Token:
        """
        Scan and return the next token from the source.

        The *previous* token is flushed to the HTML output before scanning
        begins, so the output stays in sync with the input.
        """
        self._flush_token()   # write the previous token to HTML
        self._spaces   = 0
        self._newlines = 0

        # Skip whitespace, counting for HTML re-formatting
        while True:
            if   self._ch == ' ':  self._spaces   += 1
            elif self._ch == '\t': self._spaces   += self.TAB
            elif self._ch == '\n': self._spaces    = 0; self._newlines += 1
            else:
                break
            self._ch = self._next_char()

        t  = Token()
        cc = self._char_cls[ord(self._ch)]

        if cc == self._LETTER:
            # ---- identifier or reserved keyword -------------------------
            ident = []
            while self._char_cls[ord(self._ch)] in (self._LETTER, self._DIGIT):
                if len(ident) < self.MAXNAME:
                    ident.append(self._ch)
                self._ch = self._next_char()
            if len(ident) >= self.MAXNAME:
                self.error_message("name too long")
            name = ''.join(ident)
            if name in _KEYWORD_LOOKUP:
                t.kind = _KEYWORD_LOOKUP[name]
            else:
                t.kind = TokenKind.IDENT
                t.name = name

        elif cc == self._DIGIT:
            # ---- integer literal ----------------------------------------
            num, n = 0, 0
            while self._char_cls[ord(self._ch)] == self._DIGIT:
                num = num * 10 + (ord(self._ch) - ord('0'))
                n  += 1
                self._ch = self._next_char()
            if n > self.MAXNUM:
                self.error_message("number too large")
            t.kind  = TokenKind.NUMBER
            t.value = num

        elif cc == self._COLON:
            # ---- ':='  (assignment) or stray ':' ------------------------
            self._ch = self._next_char()
            if self._ch == '=':
                self._ch = self._next_char()
                t.kind = TokenKind.ASSIGN
            else:
                t.kind = TokenKind.NUL

        elif cc == int(TokenKind.LESS):
            # ---- '<',  '<=',  '<>' -------------------------------------
            self._ch = self._next_char()
            if   self._ch == '=':
                self._ch = self._next_char(); t.kind = TokenKind.LESS_EQ
            elif self._ch == '>':
                self._ch = self._next_char(); t.kind = TokenKind.NOT_EQ
            else:
                t.kind = TokenKind.LESS

        elif cc == int(TokenKind.GREATER):
            # ---- '>',  '>=' --------------------------------------------
            self._ch = self._next_char()
            if self._ch == '=':
                self._ch = self._next_char(); t.kind = TokenKind.GREATER_EQ
            else:
                t.kind = TokenKind.GREATER

        else:
            # ---- Single-character operator / delimiter ------------------
            # cc already holds the matching TokenKind integer value.
            t.kind = TokenKind(cc) if cc in TokenKind._value2member_map_ else TokenKind.NUL
            self._ch = self._next_char()

        self.token   = t
        self._printed = False
        return t

    def check_get(self, token: Token, expected: TokenKind) -> Token:
        """
        Verify that *token* matches *expected*, then advance to the next token.

        Error recovery:
          - If both tokens are the same syntactic category (both keywords, or
            both symbols), delete *token* and treat *expected* as inserted.
          - Otherwise just insert *expected* and return *token* unchanged,
            so the parser can continue without losing its place.
        """
        if token.kind == expected:
            return self.next_token()

        both_kw  = (token.kind < TokenKind.END_OF_KEYWORD and
                    expected   < TokenKind.END_OF_KEYWORD)
        both_sym = (TokenKind.END_OF_KEYWORD <= token.kind < TokenKind.END_OF_KEYSYM and
                    TokenKind.END_OF_KEYWORD <= expected   < TokenKind.END_OF_KEYSYM)
        if both_kw or both_sym:
            self.error_delete()
            self.error_insert(expected)
            return self.next_token()

        self.error_insert(expected)
        return token

    def set_id_kind(self, kind: int) -> None:
        """
        Record the kind (CONST_ID / VAR_ID / PAR_ID / FUNC_ID) of the
        current identifier so that it can be rendered with the correct
        HTML style.
        """
        self.id_kind = kind

    # ------------------------------------------------------------------
    # HTML output helpers
    # ------------------------------------------------------------------

    def _flush_spaces(self) -> None:
        """Write accumulated newlines and spaces to the HTML output."""
        for _ in range(self._newlines): self._html.write('\n')
        for _ in range(self._spaces):   self._html.write(' ')
        self._newlines = 0
        self._spaces   = 0

    def _flush_token(self) -> None:
        """Write the current token to the HTML output (exactly once)."""
        if self._printed:
            self._printed = False
            return
        self._printed = True
        self._flush_spaces()

        k = self.token.kind
        if k < TokenKind.END_OF_KEYWORD:
            # Reserved keyword → bold
            self._html.write(f'<b>{KEYWORD_STR[k]}</b>')
        elif k < TokenKind.END_OF_KEYSYM:
            # Operator / delimiter → plain
            self._html.write(KEYWORD_STR[k])
        elif k == TokenKind.IDENT:
            name = self.token.name
            if   self.id_kind == FUNC_ID or self.id_kind == PAR_ID:
                self._html.write(f'<i>{name}</i>')       # italic
            elif self.id_kind == CONST_ID:
                self._html.write(f'<tt>{name}</tt>')     # monospace
            else:
                self._html.write(name)                   # plain (variable)
        elif k == TokenKind.NUMBER:
            self._html.write(str(self.token.value))
