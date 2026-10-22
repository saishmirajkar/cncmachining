import { HighlightStyle, StreamLanguage, syntaxHighlighting } from "@codemirror/language";
import { highlightSelectionMatches, openSearchPanel, search, searchKeymap } from "@codemirror/search";
import { EditorState, RangeSetBuilder, StateEffect, StateField } from "@codemirror/state";
import { Decoration, type DecorationSet, EditorView, keymap, lineNumbers } from "@codemirror/view";
import { tags as t } from "@lezer/highlight";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

/** Tokenizer for ISO/RS274-style G-code (comments in parentheses or after ';'). */
const gcodeLanguage = StreamLanguage.define<{ inComment: boolean }>({
  startState: () => ({ inComment: false }),
  token(stream, state) {
    if (state.inComment || stream.peek() === "(") {
      state.inComment = true;
      while (!stream.eol()) {
        if (stream.next() === ")") {
          state.inComment = false;
          break;
        }
      }
      return "comment";
    }
    if (stream.peek() === ";") {
      stream.skipToEnd();
      return "comment";
    }
    if (stream.eatSpace()) return null;
    if (stream.match("%")) return "meta";
    const m = stream.match(/^([A-Za-z])\s*[-+]?(\d+\.?\d*|\.\d+)?/);
    if (m && Array.isArray(m)) {
      const L = m[1].toUpperCase();
      if (L === "G") return "keyword";
      if (L === "M") return "atom";
      if (L === "N" || L === "O") return "labelName";
      if (L === "T" || L === "H" || L === "D") return "typeName";
      if (L === "F" || L === "S") return "number";
      return "variableName";
    }
    stream.next();
    return null;
  },
  blankLine(state) {
    state.inComment = false;
  },
});

const highlight = HighlightStyle.define([
  { tag: t.comment, color: "#7a8699", fontStyle: "italic" },
  { tag: t.keyword, color: "#2563eb", fontWeight: "600" },
  { tag: t.atom, color: "#b45309", fontWeight: "600" },
  { tag: t.labelName, color: "#94a3b8" },
  { tag: t.typeName, color: "#7c3aed", fontWeight: "600" },
  { tag: t.number, color: "#0f766e" },
  { tag: t.variableName, color: "#0f172a" },
  { tag: t.meta, color: "#be123c", fontWeight: "700" },
]);

export interface LineMark {
  line: number;
  kind: "error" | "warn" | "op";
}

const setMarks = StateEffect.define<LineMark[]>();
const setActive = StateEffect.define<number | null>();

const marksField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(deco, tr) {
    deco = deco.map(tr.changes);
    for (const e of tr.effects) {
      if (e.is(setMarks)) {
        const b = new RangeSetBuilder<Decoration>();
        const sorted = [...e.value].sort((a, b) => a.line - b.line);
        let last = -1;
        for (const m of sorted) {
          if (m.line < 1 || m.line > tr.state.doc.lines || m.line === last) continue;
          last = m.line;
          const line = tr.state.doc.line(m.line);
          b.add(line.from, line.from, Decoration.line({ class: `cm-gcode-${m.kind}-line` }));
        }
        deco = b.finish();
      }
    }
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});

const activeField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(deco, tr) {
    for (const e of tr.effects) {
      if (e.is(setActive)) {
        if (e.value === null || e.value < 1 || e.value > tr.state.doc.lines) return Decoration.none;
        const line = tr.state.doc.line(e.value);
        return Decoration.set([Decoration.line({ class: "cm-gcode-active-line" }).range(line.from)]);
      }
    }
    return deco.map(tr.changes);
  },
  provide: (f) => EditorView.decorations.from(f),
});

export interface GcodeViewerHandle {
  goToLine: (line: number) => void;
  openSearch: () => void;
}

export const GcodeViewer = forwardRef<GcodeViewerHandle, { content: string; marks?: LineMark[]; className?: string; onLineClick?: (line: number) => void }>(
  function GcodeViewer({ content, marks = [], className, onLineClick }, ref) {
    const host = useRef<HTMLDivElement>(null);
    const view = useRef<EditorView | null>(null);
    const clickRef = useRef(onLineClick);
    clickRef.current = onLineClick;

    useEffect(() => {
      if (!host.current) return;
      const v = new EditorView({
        parent: host.current,
        state: EditorState.create({
          doc: content,
          extensions: [
            lineNumbers(),
            gcodeLanguage,
            syntaxHighlighting(highlight),
            search({ top: true }),
            highlightSelectionMatches(),
            keymap.of(searchKeymap),
            EditorState.readOnly.of(true),
            EditorView.editable.of(false),
            marksField,
            activeField,
            EditorView.domEventHandlers({
              mousedown: (e, v) => {
                const pos = v.posAtCoords({ x: e.clientX, y: e.clientY });
                if (pos !== null) clickRef.current?.(v.state.doc.lineAt(pos).number);
                return false;
              },
            }),
            EditorView.theme({
              "&": { backgroundColor: "var(--surface)" },
              ".cm-gutters": { backgroundColor: "var(--surface-2)", color: "var(--muted)", borderRight: "1px solid var(--border)" },
            }),
          ],
        }),
      });
      view.current = v;
      return () => v.destroy();
    }, [content]);

    useEffect(() => {
      view.current?.dispatch({ effects: setMarks.of(marks) });
    }, [marks, content]);

    useImperativeHandle(ref, () => ({
      goToLine(line: number) {
        const v = view.current;
        if (!v || line < 1 || line > v.state.doc.lines) return;
        const l = v.state.doc.line(line);
        v.dispatch({ effects: [setActive.of(line), EditorView.scrollIntoView(l.from, { y: "center" })], selection: { anchor: l.from } });
      },
      openSearch() {
        if (view.current) openSearchPanel(view.current);
      },
    }));

    return <div ref={host} className={className} data-testid="gcode-viewer" />;
  },
);
