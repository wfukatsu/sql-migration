# Regenerate the PL/SQL parser from the vendored grammar.
#
#   make            regenerate generated/ (downloads the ANTLR tool jar on first run)
#   make fetch      re-vendor the .g4 files and Python 3 base classes from the pinned commit
#   make clean      drop the build directory (the ANTLR jar cache is kept)
#
# Requires Java 17+ and curl. Nothing here runs at install time: generated/ is committed so that
# `pip install -r requirements.txt` is enough to parse PL/SQL.

GRAMMAR_DIR  := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
REPO_ROOT    := $(abspath $(GRAMMAR_DIR)../..)
BUILD_DIR    := $(REPO_ROOT)/build/plsql-grammar
JAR_DIR      := $(REPO_ROOT)/build/antlr
ANTLR_VERSION := 4.13.2
ANTLR_JAR    := $(JAR_DIR)/antlr-$(ANTLR_VERSION)-complete.jar
ANTLR_URL    := https://www.antlr.org/download/antlr-$(ANTLR_VERSION)-complete.jar

# antlr/grammars-v4, last commit touching sql/plsql as of 2026-09-16. Keep in sync with VERSIONS.md.
GRAMMARS_V4_SHA := dc6d678008e1a6090c75adab1237d504ba7c276a
UPSTREAM := https://raw.githubusercontent.com/antlr/grammars-v4/$(GRAMMARS_V4_SHA)/sql/plsql

GENERATED := $(GRAMMAR_DIR)generated

.PHONY: all fetch clean

all: $(ANTLR_JAR)
	@rm -rf $(BUILD_DIR) && mkdir -p $(BUILD_DIR)
	cp $(GRAMMAR_DIR)PlSqlLexer.g4 $(GRAMMAR_DIR)PlSqlParser.g4 $(BUILD_DIR)/
	cp $(GRAMMAR_DIR)python3/*.py $(BUILD_DIR)/
	# 1. the .g4 actions are written for the Java target; rewrite them for Python 3
	cd $(BUILD_DIR) && python3 transformGrammar.py
	# 2. generate lexer, parser, listener and visitor
	cd $(BUILD_DIR) && java -Xmx2g -jar $(ANTLR_JAR) -Dlanguage=Python3 -visitor -o gen PlSqlLexer.g4 PlSqlParser.g4
	# 3. install into the package. ANTLR 4.13 already emits package-safe imports for the
	#    superClass; only the base classes' own inner imports need to become relative.
	@mkdir -p $(GENERATED)
	cp $(BUILD_DIR)/gen/PlSqlLexer.py $(BUILD_DIR)/gen/PlSqlParser.py \
	   $(BUILD_DIR)/gen/PlSqlParserListener.py $(BUILD_DIR)/gen/PlSqlParserVisitor.py $(GENERATED)/
	cp $(GRAMMAR_DIR)python3/PlSqlLexerBase.py $(GRAMMAR_DIR)python3/PlSqlParserBase.py $(GENERATED)/
	python3 -c "import pathlib; p = pathlib.Path('$(GENERATED)/PlSqlParserBase.py'); \
	            p.write_text(p.read_text(encoding='utf-8').replace('from PlSqlLexer import', 'from .PlSqlLexer import'), encoding='utf-8')"
	@echo "regenerated $(GENERATED)"

fetch:
	curl -fsSL -o $(GRAMMAR_DIR)PlSqlLexer.g4  $(UPSTREAM)/PlSqlLexer.g4
	curl -fsSL -o $(GRAMMAR_DIR)PlSqlParser.g4 $(UPSTREAM)/PlSqlParser.g4
	@mkdir -p $(GRAMMAR_DIR)python3
	curl -fsSL -o $(GRAMMAR_DIR)python3/PlSqlLexerBase.py   $(UPSTREAM)/Python3/PlSqlLexerBase.py
	curl -fsSL -o $(GRAMMAR_DIR)python3/PlSqlParserBase.py  $(UPSTREAM)/Python3/PlSqlParserBase.py
	curl -fsSL -o $(GRAMMAR_DIR)python3/transformGrammar.py $(UPSTREAM)/Python3/transformGrammar.py
	@echo "re-vendored from $(GRAMMARS_V4_SHA)"

# The jar is downloaded, not committed (build/ is ignored), so what arrives is checked: this is the digest of the
# jar that generated the committed generated/*.py. A different jar is a different generator.
ANTLR_SHA256 := eae2dfa119a64327444672aff63e9ec35a20180dc5b8090b7a6ab85125df4d76

$(ANTLR_JAR):
	@mkdir -p $(JAR_DIR)
	curl -fsSL -o $@.download $(ANTLR_URL)
	@echo "$(ANTLR_SHA256)  $@.download" | shasum -a 256 -c - || { rm -f $@.download; echo "ANTLR jar digest mismatch"; exit 1; }
	mv $@.download $@

clean:
	rm -rf $(BUILD_DIR)
