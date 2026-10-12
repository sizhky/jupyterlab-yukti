import type { JupyterFrontEndPlugin } from '@jupyterlab/application';
import { Clipboard } from '@jupyterlab/apputils';
import { CodeCell } from '@jupyterlab/cells';
import type { Cell, ICellModel, ICodeCellModel } from '@jupyterlab/cells';
import { IEditorServices, type IEditorMimeTypeService } from '@jupyterlab/codeeditor';
import { PageConfig, PathExt, URLExt } from '@jupyterlab/coreutils';
import {
  type ICell,
  type ILanguageInfoMetadata,
  type IOutput,
  isCode,
  isDisplayData,
  isError,
  isExecuteResult,
  isStream
} from '@jupyterlab/nbformat';
import {
  LabIcon,
  fileIcon,
  folderIcon,
  notebookIcon
} from '@jupyterlab/ui-components';
import {
  INotebookTracker,
  NotebookActions,
  type INotebookModel,
  type Notebook,
  type NotebookPanel
} from '@jupyterlab/notebook';
import { INotebookTree } from '@jupyter-notebook/tree';
import { IDefaultFileBrowser } from '@jupyterlab/filebrowser';
import { ISettingRegistry } from '@jupyterlab/settingregistry';
import { Widget } from '@lumino/widgets';

const COMM_TARGET = 'yukti.notebook_prefix';
const PLAIN_MIME_TYPE = 'text/plain';
const ASK_CLASS = 'yukti-ask-cell';
const FROZEN_CLASS = 'yukti-frozen-cell';
const HIDDEN_CLASS = 'yukti-hidden-cell';
const HIDE_TAG = 'yukti-hidden';
const SHOW_TAG = 'yukti-shown';
const WRAP_CLASS = 'yukti-wrap';
const WRAP_KEY = 'yukti_wrap';
// The column an ``%%ask`` cell wraps at, as a CSS length. ``ask-wrap.json``
// writes it, and ``none`` leaves the wrap at the width of the cell.
const WIDTH_VAR = '--yukti-ask-width';
// The colour of the rule drawn at that column, and ``transparent`` while no
// column is set, because a rule at the edge of the cell reads as a border.
const RULE_VAR = '--yukti-ask-rule';
const RULE_COLOR = 'color-mix(in srgb, var(--jp-ui-font-color1) 30%, transparent)';
const ASK_WRAP_PLUGIN_ID = 'yukti:ask-wrap';
const EDIT_CLASS = 'yukti-edit-output';
const EDITING_CLASS = 'yukti-editing';
const AREA_CLASS = 'yukti-edit-area';
const AREA_MAX_VH = 35;

/**
 * Tint one cell class with 10 percent of a theme colour.
 *
 * The two wrappers carry the tint, not the cell, because JupyterLab paints the
 * cell itself transparent while it is the active cell and would wipe the
 * shade. The editor keeps the same shade through the two variables, because an
 * edited cell gets an opaque background of its own that would cover the
 * wrapper.
 *
 * Pro: the colour arrives as a theme variable, so the light and the dark theme
 * each get a shade that sits on their own background.
 * Con: a cell that is both ``%%ask`` and frozen shows one tint only, whichever
 * rule the stylesheet states last.
 */
function tint(cls: string, color: string): string {
  return `
.${cls} .jp-Cell-inputWrapper,
.${cls} .jp-Cell-outputWrapper {
  background: color-mix(in srgb, ${color} 10%, transparent);
}
.${cls} {
  --jp-cell-editor-background:
    color-mix(in srgb, ${color} 10%, var(--jp-layout-color0));
  --jp-cell-editor-active-background: var(--jp-cell-editor-background);
}
`;
}

// Red marks the cell that spends a Codex turn, blue the cell that runs no
// more. Frozen comes last, so freezing an ``%%ask`` cell shows it as frozen.
const ASK_STYLE = tint(ASK_CLASS, 'var(--jp-error-color1)') +
  tint(FROZEN_CLASS, 'var(--jp-brand-color1)') + `
.${HIDDEN_CLASS} {
  outline: 2px dashed var(--jp-border-color0);
  outline-offset: -2px;
}
.${WRAP_CLASS} .cm-content {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.${ASK_CLASS}.${WRAP_CLASS} .cm-content {
  max-width: var(${WIDTH_VAR}, none);
  /* One 1 px column of dashes, painted as a background, so the rule costs no
     node inside the editable content and no layout. CodeMirror pads every
     line 2 px on the right, so the rule stands where the text breaks, not at
     the edge of the box. */
  background-image: repeating-linear-gradient(
    to bottom,
    var(${RULE_VAR}, transparent) 0 3px,
    transparent 3px 6px
  );
  background-size: 1px 100%;
  background-position: right 2px top;
  background-repeat: no-repeat;
}
.${ASK_CLASS} .jp-OutputArea-child {
  position: relative;
}
.${ASK_CLASS} .${EDIT_CLASS} {
  position: absolute;
  top: 0;
  right: 0;
  opacity: 0;
  padding: 2px 5px;
  border: var(--jp-border-width) solid var(--jp-border-color2);
  border-radius: var(--jp-border-radius);
  background: var(--jp-layout-color1);
  color: var(--jp-ui-font-color1);
  font-size: var(--jp-ui-font-size1);
  line-height: 1;
  cursor: pointer;
}
.${ASK_CLASS} .jp-OutputArea-child:hover .${EDIT_CLASS},
.${ASK_CLASS} .${EDIT_CLASS}:focus {
  opacity: 1;
}
.${ASK_CLASS} .${EDITING_CLASS} > .jp-OutputArea-output {
  display: none;
}
.${ASK_CLASS} .${AREA_CLASS} {
  box-sizing: border-box;
  width: 100%;
  resize: vertical;
  font-family: var(--jp-code-font-family);
  font-size: var(--jp-code-font-size);
}
`;

type SerializedOutput = { type: string; content: string };
type SerializedCell = {
  cell_id: string;
  cell_type: string;
  source: string;
  outputs?: SerializedOutput[];
};

function text(value: unknown): string {
  if (Array.isArray(value)) {
    return value.join('');
  }
  return value == null ? '' : String(value);
}

function isAskSource(source: string): boolean {
  return source.trimStart().startsWith('%%ask');
}

function asksCodex(cell: Cell): boolean {
  return (
    cell.model.type === 'code' && isAskSource(cell.model.sharedModel.getSource())
  );
}

/**
 * Whether the cell is frozen.
 *
 * ``editable: false`` is nbformat's own read-only flag, and JupyterLab already
 * honours it: a cell widget keeps ``syncEditable`` on, so the editor turns
 * read-only the moment the metadata changes, and the flag saves with the
 * .ipynb. Yukti adds the tint and the bulk-run skip on top.
 *
 * Pro: no private metadata key, and the freeze survives a reload and a copy of
 * the notebook to another machine.
 * Con: a cell that some other tool marked ``editable: false`` reads as frozen
 * here too.
 */
function isFrozen(cell: ICellModel): boolean {
  return cell.sharedModel.getMetadata('editable') === false;
}

/**
 * Whether one cell stays out of the ``%%ask`` prompt by default.
 *
 * A ``%%yukti`` cell configures Yukti and says nothing about the notebook, so
 * it costs context and answers nothing.
 */
function hiddenByDefault(cell: ICellModel): boolean {
  return (
    cell.type === 'code' &&
    cell.sharedModel.getSource().trimStart().startsWith('%%yukti')
  );
}

function cellTags(cell: ICellModel): string[] {
  const tags = cell.sharedModel.getMetadata('tags');
  return Array.isArray(tags)
    ? tags.filter((tag): tag is string => typeof tag === 'string')
    : [];
}

/**
 * Whether the cell stays out of the ``%%ask`` prompt.
 *
 * The flag is one nbformat cell tag, the same extension point nbconvert's
 * ``remove_cell_tags`` reads, so the reader can add and drop it by hand in
 * JupyterLab's own Tags panel and it saves with the .ipynb. As with
 * ``yukti_wrap``, a tag holds an override only: ``yukti-hidden`` hides a cell
 * and ``yukti-shown`` shows a ``%%yukti`` cell the default would hide.
 *
 * Pro: no private metadata key, and one tag carries both parts of the cell,
 * because a hidden cell sends neither its source nor its outputs.
 * Con: a tag list is shared ground, so a tag some other tool wrote sits beside
 * this one and a hand-edited list can hold it twice.
 */
function isHidden(cell: ICellModel): boolean {
  const tags = cellTags(cell);
  if (tags.includes(HIDE_TAG)) {
    return true;
  }
  return !tags.includes(SHOW_TAG) && hiddenByDefault(cell);
}

/**
 * Whether one cell wraps its lines by default.
 *
 * A question is prose, so an ``%%ask`` cell wraps and every other cell keeps
 * the horizontal scroll JupyterLab gives it.
 */
function wrapsByDefault(cell: ICellModel): boolean {
  return cell.type === 'code' && isAskSource(cell.sharedModel.getSource());
}

/**
 * Whether one cell wraps its lines now.
 *
 * ``yukti_wrap`` holds an override only, so an absent key reads as the
 * default and a cell that never left it saves the nbformat it had before. A
 * cell that stops being an ``%%ask`` cell therefore stops wrapping, unless the
 * reader asked that cell for a wrap by hand.
 *
 * Pro: one key, two states, and the toggle reaches every cell, not the
 * ``%%ask`` cells only.
 * Con: the override survives an edit that changes what the cell is, so a
 * question turned back into code keeps the wrap the question asked for.
 */
function wrapsLines(cell: ICellModel): boolean {
  const value = cell.sharedModel.getMetadata(WRAP_KEY);
  return typeof value === 'boolean' ? value : wrapsByDefault(cell);
}

function serializeOutput(output: IOutput): SerializedOutput {
  if (isStream(output)) {
    return { type: output.output_type, content: text(output.text) };
  }
  if (isError(output)) {
    return { type: output.output_type, content: output.traceback.join('\n') };
  }
  if (isDisplayData(output) || isExecuteResult(output)) {
    const data = output.data;
    const visible = data['text/markdown'] ?? data['text/plain'] ??
      data['application/json'] ?? data;
    return {
      type: output.output_type,
      content: typeof visible === 'string' ? visible :
        (JSON.stringify(visible, null, 2) ?? '')
    };
  }
  return { type: output.output_type, content: JSON.stringify(output) };
}

function serializeCell(cellId: string, cell: ICell): SerializedCell {
  const serialized: SerializedCell = {
    cell_id: cellId,
    cell_type: cell.cell_type,
    source: text(cell.source)
  };
  if (isCode(cell)) {
    serialized.outputs = cell.outputs.map(serializeOutput);
  }
  return serialized;
}

/**
 * Find a cell by the id the kernel minted for it.
 *
 * ``insert_cells`` carries that id, and ``createCell`` in ``@jupyter/ydoc``
 * adopts it, so both sides name the same cell without the frontend answering
 * a comm the kernel cannot read while it is busy with the ``%%ask`` cell.
 *
 * The caller passes the cells below the question, so a run reaches only a cell
 * Yukti inserted, never one the reader wrote above it.
 */
function findCell(cells: readonly Cell[], cellId: unknown): Cell | null {
  if (typeof cellId !== 'string') {
    return null;
  }
  return cells.find(widget => widget.model.id === cellId) ?? null;
}

/**
 * Show that the kernel is inside this cell.
 *
 * The prompt is the only sign a reader gets, because the run belongs to the
 * ``%%ask`` cell's ``execute_request`` and this cell has none of its own.
 */
function markRunning(cell: Cell): void {
  if (cell.model.type !== 'code') {
    return;
  }
  (cell.model as ICodeCellModel).outputs.clear();
  (cell as CodeCell).setPrompt('*');
}

/**
 * Paint one run's outputs into the cell that holds the source.
 *
 * The outputs are nbformat, so an image, a figure and a traceback all render
 * the way JupyterLab renders them for an ordinary run. The execution count is
 * the one the ``%%ask`` cell is using, because that is the request this run
 * happened inside.
 *
 * Pro: the reader sees the output under the code, not in the question.
 * Con: two cells then show the same count.
 */
function showRun(cell: Cell, outputs: unknown, count: unknown): void {
  if (cell.model.type !== 'code' || !Array.isArray(outputs)) {
    return;
  }
  const model = cell.model as ICodeCellModel;
  model.outputs.clear();
  outputs.forEach(value => {
    const output = value as Record<string, unknown> | null;
    if (output != null && typeof output.output_type === 'string') {
      model.outputs.add(output as unknown as IOutput);
    }
  });
  model.executionCount = typeof count === 'number' ? count : null;
}

/**
 * The two voices ``AI Mode`` switches between, and the metadata key that
 * remembers which one this notebook asked for.
 *
 * The kernel holds the prompt each name stands for, so a new mode is one
 * entry here and one entry in ``MODES`` of ``app_server.py``.
 */
const MODES = [
  { value: 'concise', label: 'Concise' },
  { value: 'learning', label: 'Learning' }
];
const MODE_KEY = 'yukti_mode';
const MODE_CLASS = 'yukti-mode';
const MODE_STYLE = `
.${MODE_CLASS} {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 4px;
}
.${MODE_CLASS} > span {
  color: var(--jp-ui-font-color1);
  font-size: var(--jp-ui-font-size1);
  white-space: nowrap;
}
`;

/**
 * Read the mode one notebook asked for, and fall back to the first one.
 *
 * A file the reader edited by hand can name anything, so the name is checked
 * here as well as in the kernel.
 */
function readMode(model: INotebookModel | null): string {
  const value = model?.getMetadata(MODE_KEY);
  return MODES.some(mode => mode.value === value)
    ? (value as string)
    : MODES[0].value;
}

/**
 * The ``AI Mode`` dropdown of one notebook's toolbar.
 *
 * The choice lives in the notebook's own metadata, so it survives a reload and
 * travels with the file, and the prefix comm reads it off the same model it
 * already holds instead of through a second channel.
 *
 * Pro: one notebook can ask for a mentor while the next one asks for answers.
 * Con: a new notebook starts at the first mode again, because a file carries
 * no user preference.
 */
function modeWidget(panel: NotebookPanel): Widget {
  const node = document.createElement('div');
  node.className = MODE_CLASS;
  const label = document.createElement('span');
  label.textContent = 'AI Mode';
  const select = document.createElement('select');
  select.className = 'jp-mod-styled';
  select.title = 'The voice every %%ask cell answers in';
  select.setAttribute('aria-label', 'AI Mode');
  for (const mode of MODES) {
    const option = document.createElement('option');
    option.value = mode.value;
    option.textContent = mode.label;
    select.appendChild(option);
  }
  select.addEventListener('change', () => {
    panel.model?.setMetadata(MODE_KEY, select.value);
  });
  // The metadata of a notebook that is still loading is empty, so the value
  // is read once the file has landed.
  panel.context.ready.then(() => {
    select.value = readMode(panel.model);
  });
  node.append(label, select);
  return new Widget({ node });
}

/**
 * One dropdown per notebook toolbar, next to the cell type.
 *
 * Pro: the mode sits where the reader already looks for a per-notebook
 * choice, and one glance says which voice the next question gets.
 * Con: the item is inserted by hand, so a JupyterLab release that renames the
 * ``cellType`` item moves the dropdown to the end of the toolbar.
 */
const aiModePlugin: JupyterFrontEndPlugin<void> = {
  id: 'yukti:ai-mode',
  autoStart: true,
  requires: [INotebookTracker],
  activate: (_app, tracker: INotebookTracker) => {
    const style = document.createElement('style');
    style.textContent = MODE_STYLE;
    document.head.appendChild(style);
    tracker.widgetAdded.connect((_sender, panel: NotebookPanel) => {
      const widget = modeWidget(panel);
      if (!panel.toolbar.insertAfter('cellType', MODE_CLASS, widget)) {
        panel.toolbar.addItem(MODE_CLASS, widget);
      }
    });
  }
};

const plugin: JupyterFrontEndPlugin<void> = {
  id: 'yukti:notebook-prefix',
  autoStart: true,
  requires: [INotebookTracker],
  activate: (_app, tracker: INotebookTracker) => {
    NotebookActions.executionScheduled.connect((_sender, { cell, notebook }) => {
      const source = text(cell.model.toJSON().source);
      if (!isAskSource(source) || isFrozen(cell.model)) {
        return;
      }

      const index = notebook.widgets.indexOf(cell);
      const panel = tracker.find(candidate => candidate.content === notebook);
      const kernel = panel?.sessionContext.session?.kernel;
      if (index < 0 || kernel == null) {
        return;
      }

      // A hidden cell drops out here, which is the one place the prompt is
      // built, so Codex reads neither its source nor its outputs and cannot
      // name it in a ``replace_cells`` call either.
      const cells = notebook.widgets
        .slice(0, index)
        .filter(widget => !isHidden(widget.model))
        .map(widget => serializeCell(widget.model.id, widget.model.toJSON()));
      const requestId = `${cell.model.id}:${Date.now()}`;
      const comm = kernel.createComm(COMM_TARGET);
      let lastInsertedCellId: string | undefined;
      comm.onMsg = message => {
        const data = message.content.data;
        if (
          data.request_id !== requestId ||
          notebook.model == null
        ) {
          return;
        }

        const currentIndex = notebook.widgets.indexOf(cell);
        if (currentIndex < 0) {
          return;
        }
        if (data.type === 'insert_cells' && Array.isArray(data.cells)) {
          const inserted = data.cells.map(value => {
            if (typeof value !== 'object' || value == null || Array.isArray(value)) {
              return null;
            }
            const candidate = value as Record<string, unknown>;
            const cellType = candidate.cell_type;
            return (
              (cellType === 'code' || cellType === 'markdown') &&
              typeof candidate.source === 'string' &&
              typeof candidate.cell_id === 'string'
            ) ? {
              id: candidate.cell_id,
              cell_type: cellType,
              source: candidate.source,
              metadata: cellType === 'code' ? { trusted: false } : {}
            } : null;
          });
          if (inserted.length === 0 || inserted.some(cell => cell == null)) {
            return;
          }
          const previousIndex = notebook.model.sharedModel.cells.findIndex(
            sharedCell => sharedCell.id === lastInsertedCellId
          );
          const insertionIndex = previousIndex < 0
            ? currentIndex + 1
            : previousIndex + 1;
          notebook.model.sharedModel.transact(() => {
            const newCells = notebook.model!.sharedModel.insertCells(
              insertionIndex,
              inserted.map(newCell => newCell!)
            );
            lastInsertedCellId = newCells.at(-1)!.id;
          });
          notebook.activeCellIndex = currentIndex + 1;
          return;
        }
        if (data.type === 'run_cells' && Array.isArray(data.cells)) {
          const below = notebook.widgets.slice(currentIndex + 1);
          data.cells.forEach(value => {
            const named = value as Record<string, unknown> | null;
            const cell = findCell(below, named?.cell_id);
            if (cell != null) {
              markRunning(cell);
            }
          });
          return;
        }
        if (data.type === 'cell_output') {
          const below = notebook.widgets.slice(currentIndex + 1);
          const cell = findCell(below, data.cell_id);
          if (cell != null) {
            showRun(cell, data.outputs, data.execution_count);
          }
          return;
        }
        if (data.type !== 'replace_cells' || !Array.isArray(data.cells)) {
          return;
        }

        // Every cell but the question: the cells above it are the transcript
        // Codex read, and the cells below are the ones it inserted in this
        // turn, which it rewrites when a run of one of them fails.
        const editable = new Map(
          notebook.widgets
            .filter((_widget, index) => index !== currentIndex)
            .map(widget => [widget.model.id, widget])
        );
        const edits = data.cells.map(value => {
          if (typeof value !== 'object' || value == null || Array.isArray(value)) {
            return null;
          }
          const replacement = value as Record<string, unknown>;
          const widget = editable.get(replacement.cell_id as string);
          return typeof replacement.source === 'string' && widget != null
            ? { widget, source: replacement.source }
            : null;
        });
        if (edits.length === 0 || edits.some(edit => edit == null)) {
          return;
        }
        notebook.model.sharedModel.transact(() => {
          edits.forEach(edit => edit!.widget.model.sharedModel.setSource(edit!.source));
        });
        notebook.activeCellIndex = notebook.widgets.indexOf(edits[0]!.widget);
      };
      comm.open({
        type: 'notebook_prefix',
        request_id: requestId,
        cells,
        mode: readMode(notebook.model)
      });
    });
  }
};

/**
 * Show every ``%%ask`` cell as a question: no Python highlighting, and the
 * ``ASK_CLASS`` the stylesheet tints.
 *
 * A cell editor takes its language from ``cell.model.mimeType``, so
 * ``text/plain`` leaves the prompt unhighlighted. The notebook resets that
 * mimetype when a cell arrives and when ``language_info`` lands, so both paths
 * re-sync. The class goes on the cell widget, which the notebook creates once
 * per cell and keeps while windowing attaches and detaches its node.
 *
 * Pro: one prefix test per keystroke, and the mimetype setter ignores
 * same-value writes, so CodeMirror reloads its language only when the cell
 * crosses the ``%%ask`` boundary.
 * Con: the mimetype is also what completion and tooltips read, so an ``%%ask``
 * cell loses Python completions too; and one keystroke repaints the class of
 * every cell, because a cell model does not name its widget.
 */
function trackAskCells(
  panel: NotebookPanel,
  mimeTypes: IEditorMimeTypeService
): void {
  const model = panel.model;
  if (model == null) {
    return;
  }

  const codeMimeType = (): string | null => {
    const info = model.getMetadata('language_info');
    return info == null ? null : mimeTypes.getMimeTypeByLanguage(info);
  };

  const sync = (cell: ICellModel): void => {
    if (cell.type !== 'code') {
      return;
    }
    const mimeType = isAskSource(cell.sharedModel.getSource())
      ? PLAIN_MIME_TYPE
      : codeMimeType();
    if (mimeType != null) {
      cell.mimeType = mimeType;
    }
  };

  const paint = (): void => {
    for (const widget of panel.content.widgets) {
      widget.node.classList.toggle(ASK_CLASS, asksCodex(widget));
      widget.node.classList.toggle(FROZEN_CLASS, isFrozen(widget.model));
      widget.node.classList.toggle(HIDDEN_CLASS, isHidden(widget.model));
      widget.node.classList.toggle(WRAP_CLASS, wrapsLines(widget.model));
    }
  };

  const watch = (cell: ICellModel): void => {
    sync(cell);
    cell.contentChanged.connect(sync);
    cell.contentChanged.connect(paint);
    cell.metadataChanged.connect(paint);
  };

  for (const cell of model.cells) {
    watch(cell);
  }
  paint();
  // A move or a delete repaints too, because the widgets and the models keep
  // one order and a cell that changed place keeps its own text.
  model.cells.changed.connect((_, change) => {
    if (change.type === 'add' || change.type === 'set') {
      change.newValues.forEach(watch);
    }
    paint();
  });
  model.metadataChanged.connect((_, change) => {
    if (change.key === 'language_info') {
      for (const cell of model.cells) {
        sync(cell);
      }
    }
  });
}

const plainTextPlugin: JupyterFrontEndPlugin<void> = {
  id: 'yukti:ask-plain-text',
  autoStart: true,
  requires: [INotebookTracker, IEditorServices],
  activate: (
    _app,
    tracker: INotebookTracker,
    editorServices: IEditorServices
  ) => {
    // One style element, because the tint is two rules and a separate CSS
    // file would ask the labextension build for a second entry point.
    const style = document.createElement('style');
    style.textContent = ASK_STYLE;
    document.head.appendChild(style);
    tracker.widgetAdded.connect((_sender, panel) => {
      void panel.context.ready.then(() => {
        if (!panel.isDisposed) {
          trackAskCells(panel, editorServices.mimeTypeService);
        }
      });
    });
  }
};

/**
 * The cells each bulk run action would run, one entry per action name.
 *
 * The slices repeat what ``NotebookActions`` does, because those actions hand
 * their cells to a private function that no extension can reach.
 */
const BULK_RUNS: Record<string, (notebook: Notebook) => Cell[]> = {
  runAll: notebook => notebook.widgets.slice(),
  runAllAbove: notebook => notebook.widgets.slice(0, notebook.activeCellIndex),
  runAllBelow: notebook => notebook.widgets.slice(notebook.activeCellIndex)
};

type RunCells = (
  notebook: Notebook,
  cells: readonly Cell[],
  ...rest: unknown[]
) => Promise<boolean>;

/**
 * Leave every ``%%ask`` cell and every frozen cell out of a bulk run.
 *
 * ``NotebookActions.runCells`` is the only public entry that names its cells,
 * and both "Restart Kernel and Run …" commands call it, so the filter lives
 * there. "Run All Cells" and its Above and Below variants reach a private
 * function instead, so each one is replaced by its slice plus that filter.
 * Menu, toolbar and shortcut then agree, because all of them go through these
 * four names.
 *
 * A named run still asks: Shift+Enter and "Run Selected Cells" go through a
 * private function that no extension can reach, so those two paths stay as
 * they are. A frozen cell is stopped later instead, by ``blockFrozenRuns``.
 *
 * Pro: running the whole notebook costs no Codex turn and inserts no cell, so
 * an ``%%ask`` cell keeps the answer it already holds.
 * Con: the actions are patched for every notebook in the JupyterLab session,
 * and a release that changes one of those slices changes it here too.
 */
function skipAskOnBulkRun(): void {
  const actions = NotebookActions as unknown as Record<string, unknown>;
  const runCells = actions.runCells as RunCells | undefined;
  // A renamed action leaves JupyterLab's own behaviour in place, because one
  // Codex turn too many beats a Run All that runs nothing.
  if (typeof runCells !== 'function') {
    return;
  }
  const runOthers: RunCells = (notebook, cells, ...rest) => {
    const kept = cells.filter(
      cell => !asksCodex(cell) && !isFrozen(cell.model)
    );
    return kept.length === 0
      ? Promise.resolve(false)
      : runCells(notebook, kept, ...rest);
  };
  actions.runCells = runOthers;
  for (const [name, slice] of Object.entries(BULK_RUNS)) {
    if (typeof actions[name] !== 'function') {
      continue;
    }
    actions[name] = (notebook: Notebook, ...rest: unknown[]) => {
      if (notebook.model == null) {
        return Promise.resolve(false);
      }
      const done = runOthers(notebook, slice(notebook), ...rest);
      notebook.deselectAll();
      return done;
    };
  }
}

type Execute = typeof CodeCell.execute;

/**
 * Let no frozen cell reach the kernel.
 *
 * ``CodeCell.execute`` is the one call every run path ends at: Shift+Enter,
 * Ctrl+Enter, Alt+Enter, "Run Selected Cells", each Run All, and each "Restart
 * Kernel and Run …". Two of those paths go through a private function that no
 * extension can reach, so the guard sits at the shared end instead of at the
 * six starts.
 *
 * Skipping returns ``undefined``, which the signature already allows for a
 * cell with no reply, so JupyterLab reads the cell as run and walks on to the
 * next one. Nothing writes the ``*`` prompt, because that write lives inside
 * the call that is skipped.
 *
 * Pro: one patch covers every present path and every path a later release
 * adds, and the cell keeps the output it already holds.
 * Con: the skip is silent, so the tint and the read-only editor are the only
 * signs the cell did not run.
 */
function blockFrozenRuns(): void {
  const cells = CodeCell as unknown as Record<string, unknown>;
  const execute = cells.execute as Execute | undefined;
  // A renamed static leaves JupyterLab's own behaviour in place, because a
  // frozen cell that runs beats a notebook that cannot run at all.
  if (typeof execute !== 'function') {
    return;
  }
  cells.execute = (cell: CodeCell, ...rest: unknown[]) =>
    isFrozen(cell.model)
      ? Promise.resolve(undefined)
      : (execute as (...args: unknown[]) => unknown)(cell, ...rest);
}

const skipAskPlugin: JupyterFrontEndPlugin<void> = {
  id: 'yukti:skip-ask-on-run-all',
  autoStart: true,
  activate: () => {
    skipAskOnBulkRun();
    blockFrozenRuns();
  }
};

/**
 * Edit one output of an ``%%ask`` cell in place.
 *
 * The block is saved as a single markdown output, so the notebook shows the
 * new text and ``serializeCell`` sends that same text to the next turn. It
 * stays an ordinary output, so Codex reads the correction as the answer this
 * cell holds.
 *
 * Enter, in every combination but Alt+Enter, closes the box and saves, and so
 * does leaving it; Alt+Enter types a line and Escape cancels. Every keystroke
 * stops at the box: JupyterLab reads keydown while the event bubbles, so this
 * is what keeps Shift+Enter from running the cell and ``dd`` from deleting it.
 *
 * The box opens as tall as the output it replaces, and never taller than
 * ``AREA_MAX_VH`` percent of the window, so a long answer stays scrollable
 * instead of pushing the notebook off screen.
 *
 * Pro: a wrong answer is corrected where it is read, and the .ipynb keeps it
 * like any other output.
 * Con: the block loses its other mime types, so an edited tool call becomes a
 * plain fenced block, and running the cell again clears the correction.
 */
function editOutput(model: ICodeCellModel, index: number, host: Element): void {
  const outputs = model.outputs;
  const output = outputs.get(index);
  if (output == null) {
    return;
  }
  const shown = host.querySelector('.jp-OutputArea-output');
  const tall = Math.round(shown?.getBoundingClientRect().height ?? 0);
  const area = document.createElement('textarea');
  area.className = AREA_CLASS;
  area.value = serializeOutput(output.toJSON()).content;
  area.style.height = `min(max(${tall}px, 2em), ${AREA_MAX_VH}vh)`;
  host.classList.add(EDITING_CLASS);
  host.appendChild(area);
  area.focus();

  // The blur that follows a save or a cancel must not write a second time.
  const finish = (save: boolean): void => {
    if (!host.classList.contains(EDITING_CLASS)) {
      return;
    }
    host.classList.remove(EDITING_CLASS);
    const written = area.value;
    area.remove();
    if (save) {
      outputs.set(index, {
        output_type: 'display_data',
        data: { 'text/markdown': written },
        metadata: {}
      });
    }
  };

  area.addEventListener('keydown', event => {
    event.stopPropagation();
    if (event.key === 'Escape') {
      finish(false);
      return;
    }
    if (event.key === 'Enter' && !event.altKey) {
      event.preventDefault();
      finish(true);
    }
  });
  area.addEventListener('blur', () => finish(true));
}

/**
 * Offer one pencil on the output block the pointer is over, inside ``%%ask``
 * cells only.
 *
 * One button follows the pointer instead of one button per block, because an
 * output area rebuilds its children while the turn streams and would drop
 * every button it holds.
 *
 * Pro: nothing to keep in step with the outputs, and no button reaches a cell
 * that never asked.
 * Con: the pencil sits over the top right of the block, so it can cover a
 * word until the pointer leaves.
 */
function trackOutputEdits(panel: NotebookPanel): void {
  const pencil = document.createElement('button');
  pencil.className = EDIT_CLASS;
  pencil.textContent = '✎';
  pencil.title = 'Edit this output';
  pencil.setAttribute('aria-label', 'Edit this output');

  panel.node.addEventListener('mouseover', event => {
    const target = event.target as Element | null;
    const host = target?.closest('.jp-OutputArea-child') ?? null;
    if (host == null || host.closest(`.${ASK_CLASS}`) == null) {
      return;
    }
    if (host !== pencil.parentElement) {
      host.appendChild(pencil);
    }
  });

  pencil.addEventListener('click', () => {
    const host = pencil.parentElement;
    const area = host?.parentElement;
    if (host == null || area == null) {
      return;
    }
    const blocks = Array.from(area.children).filter(node =>
      node.classList.contains('jp-OutputArea-child')
    );
    const index = blocks.indexOf(host);
    const cell = panel.content.widgets.find(widget => widget.node.contains(host));
    if (index >= 0 && cell != null && cell.model.type === 'code') {
      editOutput(cell.model as ICodeCellModel, index, host);
    }
  });
}

const editOutputPlugin: JupyterFrontEndPlugin<void> = {
  id: 'yukti:edit-ask-output',
  autoStart: true,
  requires: [INotebookTracker],
  activate: (_app, tracker: INotebookTracker) => {
    tracker.widgetAdded.connect((_sender, panel) => {
      trackOutputEdits(panel);
    });
  }
};

type CopyPart = 'input' | 'output' | 'both';

const COPY_COMMANDS: { id: string; label: string; part: CopyPart }[] = [
  { id: 'yukti:copy-input', label: 'Copy Input', part: 'input' },
  { id: 'yukti:copy-output', label: 'Copy Output', part: 'output' },
  { id: 'yukti:copy-both', label: 'Copy Input and Output', part: 'both' }
];

/**
 * Wrap a block in a code fence.
 *
 * The fence grows past the longest backtick run inside the block, which
 * CommonMark allows, so a copied ``%%ask`` answer that holds its own fences
 * still pastes as one block.
 *
 * Pro: three backticks stay the common case, so an ordinary paste looks normal.
 * Con: a reader that predates CommonMark sees the longer fence as literal text.
 */
function fence(body: string, language: string): string {
  const runs = body.match(/`+/g) ?? [];
  const ticks = '`'.repeat(Math.max(3, ...runs.map(run => run.length + 1)));
  return `${ticks}${language}\n${body}\n${ticks}`;
}

/**
 * Render one cell as clipboard text.
 *
 * Every part arrives inside a code fence, so a paste keeps the cell's shape in
 * every markdown reader. An empty output leaves the source fence alone.
 *
 * Pro: the clipboard reuses ``serializeOutput``, so a paste shows the same
 * output text that ``%%ask`` sends to Codex.
 * Con: a rich output arrives as its ``text/plain`` repr, so a copied DataFrame
 * loses the HTML table.
 */
function copyText(language: string, cell: ICell, part: CopyPart): string {
  const source = fence(text(cell.source), language);
  if (part === 'input') {
    return source;
  }
  const outputs = isCode(cell)
    ? cell.outputs.map(output => serializeOutput(output).content).join('\n')
    : '';
  const fenced = outputs === '' ? '' : fence(outputs, '');
  if (part === 'output') {
    return fenced;
  }
  return fenced === '' ? source : `${source}\n\n${fenced}`;
}

/**
 * Draw the cell as two bars: the top bar is the source, the bottom bar is the
 * output, and a solid bar marks the part the button copies.
 *
 * The outline bar sets ``fill`` through ``style`` instead of the attribute,
 * because JupyterLab themes an icon with ``.jp-icon3[fill]`` and that selector
 * would repaint an empty bar solid.
 *
 * Pro: the glyph repeats the shape already on screen, and three of them cost
 * about 72 px, so a short cell keeps its toolbar instead of hiding it.
 * Con: nothing in the glyph says "clipboard", so the caption carries that.
 */
function bandIcon(part: CopyPart): LabIcon {
  const bar = (y: number, solid: boolean): string =>
    `<rect x="2" y="${y}" width="12" height="5" rx="1.5" class="jp-icon3" ` +
    (solid
      ? 'fill="#616161"'
      : 'style="fill:none" stroke="#616161" stroke-width="1.2"') +
    '/>';
  return new LabIcon({
    name: `yukti:copy-${part}`,
    svgstr:
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">' +
      bar(2.5, part !== 'output') +
      bar(8.5, part !== 'input') +
      '</svg>'
  });
}

/**
 * Add three copy commands, which ``schema/cell-copy.json`` places in the cell
 * toolbar.
 *
 * Pro: JupyterLab owns the buttons, so they keep the native look, reach the
 * command palette, and accept keyboard shortcuts.
 * Con: one cell toolbar exists at a time and it follows the active cell, so the
 * buttons appear on the clicked cell, not on the hovered cell.
 */
const copyPlugin: JupyterFrontEndPlugin<void> = {
  id: 'yukti:cell-copy',
  autoStart: true,
  requires: [INotebookTracker],
  activate: (app, tracker: INotebookTracker) => {
    for (const { id, label, part } of COPY_COMMANDS) {
      app.commands.addCommand(id, {
        label,
        icon: bandIcon(part),
        caption: `${label} of the active cell to the clipboard`,
        isEnabled: () => tracker.activeCell != null,
        execute: () => {
          const panel = tracker.currentWidget;
          const cell = panel?.content.activeCell?.model.toJSON();
          if (panel == null || cell == null) {
            return;
          }
          const info = panel.model?.getMetadata('language_info') as
            | ILanguageInfoMetadata
            | undefined;
          const language = isCode(cell)
            ? info?.name ?? ''
            : cell.cell_type;
          Clipboard.copyToSystem(copyText(language, cell, part));
        }
      });
    }
  }
};

/**
 * One padlock, closed while the cell is frozen.
 *
 * The glyph repeats the copy buttons' 16 px box and their ``jp-icon3`` class,
 * so the theme paints every button the same grey.
 */
function padlockIcon(locked: boolean): LabIcon {
  const shackle = locked ? 'M5.2 7V5.2a2.8 2.8 0 0 1 5.6 0V7' :
    'M5.2 7V5.2a2.8 2.8 0 0 1 5.6 0';
  return new LabIcon({
    name: `yukti:freeze-${locked ? 'on' : 'off'}`,
    svgstr:
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">' +
      `<path d="${shackle}" class="jp-icon3" style="fill:none" ` +
      'stroke="#616161" stroke-width="1.3"/>' +
      '<rect x="3" y="7" width="10" height="7" rx="1.5" class="jp-icon3" ' +
      'fill="#616161"/></svg>'
  });
}

/**
 * One eye, open while the cell reaches Codex.
 *
 * The glyph repeats the copy buttons' 16 px box and their ``jp-icon3`` class,
 * so the theme paints every button the same grey.
 */
function eyeIcon(open: boolean): LabIcon {
  const grey = 'class="jp-icon3" style="fill:none" stroke="#616161" ';
  return new LabIcon({
    name: `yukti:hide-${open ? 'off' : 'on'}`,
    svgstr:
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">' +
      '<path d="M1.4 8S4.3 3.9 8 3.9 14.6 8 14.6 8 11.7 12.1 8 12.1 1.4 8 ' +
      `1.4 8Z" ${grey}stroke-width="1.3"/>` +
      '<circle cx="8" cy="8" r="1.9" class="jp-icon3" fill="#616161"/>' +
      (open ? '' : `<path d="M2.8 13.2 13.2 2.8" ${grey}stroke-width="1.5"/>`) +
      '</svg>'
  });
}

/**
 * Three lines, the middle one turning back while the cell wraps and running
 * off to the right while it does not.
 *
 * The glyph repeats the copy buttons' 16 px box and their ``jp-icon3`` class,
 * so the theme paints every button the same grey.
 */
function wrapIcon(wrapped: boolean): LabIcon {
  const grey = 'class="jp-icon3" style="fill:none" stroke="#616161" ' +
    'stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" ';
  const middle = wrapped
    ? 'M2.5 8h8a2 2 0 0 1 0 4H6.5m1.6-1.6L6.5 12l1.6 1.6'
    : 'M2.5 10h9.5m-1.6-1.6L12 10l-1.6 1.6';
  return new LabIcon({
    name: `yukti:wrap-${wrapped ? 'on' : 'off'}`,
    svgstr:
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">' +
      `<path d="M2.5 4h11" ${grey}/>` +
      `<path d="${middle}" ${grey}/>` +
      '</svg>'
  });
}

// Both padlocks, both eyes and both wraps are built once, because ``LabIcon``
// keys its instances by name and warns about a second icon under a name it
// already holds.
const PADLOCKS = { on: padlockIcon(true), off: padlockIcon(false) };
const EYES = { on: eyeIcon(true), off: eyeIcon(false) };
const WRAPS = { on: wrapIcon(true), off: wrapIcon(false) };

type CellFlag = {
  /** The command id ``schema/cell-copy.json`` names. */
  command: string;
  /** Whether the flag sits on this cell. */
  read: (cell: ICellModel) => boolean;
  /** Set the flag, or clear it. */
  write: (cell: ICellModel, on: boolean) => void;
  /** The glyph, the menu label and the tooltip, each for both states. */
  icon: (on: boolean) => LabIcon;
  label: (on: boolean) => string;
  caption: (on: boolean) => string;
};

/**
 * One toolbar button that turns one cell flag on and off.
 *
 * Every part that differs between two such buttons arrives in ``flag``, so the
 * command wiring is written once: the label, the glyph and the tooltip all
 * read the active cell, so the button has to be told when that cell changes
 * and when its own click lands.
 *
 * Pro: JupyterLab owns the button, so it keeps the native look, reaches the
 * command palette, and accepts a keyboard shortcut.
 * Con: one cell toolbar exists at a time and it follows the active cell, so
 * the button acts on the clicked cell, never on the hovered one.
 */
function flagPlugin(id: string, flag: CellFlag): JupyterFrontEndPlugin<void> {
  return {
    id,
    autoStart: true,
    requires: [INotebookTracker],
    activate: (app, tracker: INotebookTracker) => {
      const on = (): boolean => {
        const cell = tracker.activeCell;
        return cell != null && flag.read(cell.model);
      };
      app.commands.addCommand(flag.command, {
        label: () => flag.label(on()),
        icon: () => flag.icon(on()),
        caption: () => flag.caption(on()),
        isEnabled: () => tracker.activeCell != null,
        isToggled: on,
        execute: () => {
          const cell = tracker.activeCell;
          if (cell == null) {
            return;
          }
          flag.write(cell.model, !flag.read(cell.model));
          app.commands.notifyCommandChanged(flag.command);
        }
      });
      tracker.activeCellChanged.connect(() => {
        app.commands.notifyCommandChanged(flag.command);
      });
    }
  };
}

/**
 * Freeze and unfreeze the active cell.
 *
 * Freezing writes ``editable: false`` and ``deletable: false``, so JupyterLab
 * turns the editor read-only and refuses to delete or cut the cell,
 * ``trackAskCells`` tints it, and ``skipAskOnBulkRun`` leaves it out of every
 * Run All. Unfreezing deletes both keys instead of writing ``true``, so an
 * unfrozen cell saves the same nbformat it had before.
 *
 * Pro: an expensive cell keeps its output through every run path and through
 * a stray delete, and the freeze travels with the .ipynb.
 * Con: a frozen cell still runs from the debugger's own entry points and from
 * any code that calls the kernel directly, because ``blockFrozenRuns`` guards
 * ``CodeCell.execute`` only. A cut of many cells drops the others and leaves
 * the frozen one behind, which reads as a failed cut until you look.
 */
const freezePlugin = flagPlugin('yukti:freeze-cell', {
  command: 'yukti:toggle-freeze',
  read: isFrozen,
  // ``deletable`` travels with ``editable``, because a frozen cell that one
  // Backspace still removes keeps nothing. JupyterLab reads both flags itself:
  // ``deleteCells`` and ``cut`` skip a cell whose ``deletable`` is false, so
  // the trash button, the ``dd`` shortcut and the Edit menu all leave it in
  // place. Unfreezing deletes both keys, and deleting a key that is not there
  // is a no-op, so a cell frozen before this change unfreezes cleanly.
  write: (cell, on) => {
    for (const key of ['editable', 'deletable']) {
      if (on) {
        cell.sharedModel.setMetadata(key, false);
      } else {
        cell.sharedModel.deleteMetadata(key);
      }
    }
  },
  icon: frozen => (frozen ? PADLOCKS.on : PADLOCKS.off),
  label: frozen => (frozen ? 'Unfreeze Cell' : 'Freeze Cell'),
  caption: frozen =>
    frozen
      ? 'Unfreeze the active cell, so it edits and runs again'
      : 'Freeze the active cell: read-only, and skipped by Run All'
});

/**
 * Keep the active cell out of the next question's prompt, or put it back.
 *
 * Hiding adds the ``yukti-hidden`` tag, which drops the cell from the prefix
 * the ``%%ask`` cell sends and draws a dashed outline around the cell. The
 * outline marks the cell without touching its text, so a hidden cell stays as
 * readable as every other one. Showing again drops the tag,
 * and drops the whole ``tags`` list when nothing else is in it, so a notebook
 * that never used tags saves the nbformat it had before. A ``%%yukti`` cell
 * starts hidden, so showing it writes ``yukti-shown`` and hiding it again
 * drops that tag.
 *
 * The cell itself is untouched: it edits, runs and prints as it always did,
 * because hiding speaks to Codex only.
 *
 * Pro: a long log or a dead end costs no context, and the reader still sees it.
 * Con: Codex reads a notebook with a hole in it, so a hidden import or a
 * hidden variable can make the answer name something it cannot see.
 */
const hidePlugin = flagPlugin('yukti:hide-cell', {
  command: 'yukti:toggle-hidden',
  read: isHidden,
  write: (cell, on) => {
    const kept = cellTags(cell).filter(
      tag => tag !== HIDE_TAG && tag !== SHOW_TAG
    );
    const override =
      on === hiddenByDefault(cell) ? [] : [on ? HIDE_TAG : SHOW_TAG];
    const next = [...kept, ...override];
    if (next.length === 0) {
      cell.sharedModel.deleteMetadata('tags');
    } else {
      cell.sharedModel.setMetadata('tags', next);
    }
  },
  icon: hidden => (hidden ? EYES.off : EYES.on),
  label: hidden => (hidden ? 'Show Cell to Codex' : 'Hide Cell from Codex'),
  caption: hidden =>
    hidden
      ? 'Send this cell to Codex again with the next question'
      : 'Leave this cell out of every %%ask prompt: no source, no output'
});

/**
 * The folder that holds the notebook, ``''`` at the server's root.
 */
function notebookDir(panel: NotebookPanel | null): string | null {
  return panel == null ? null : PathExt.dirname(panel.context.path);
}

/**
 * Two File menu entries for the current notebook's folder, which
 * ``schema/file-menu.json`` places under New.
 *
 * "Open in CWD" opens ``<base>/tree/<folder>``, the address Jupyter Notebook
 * serves its file browser on, in a second browser tab, so the notebook keeps
 * its own tab and its kernel. "New Notebook Here" writes an untitled notebook
 * beside the current one and opens it on the same kernel name, because a
 * sibling notebook almost always wants the environment its neighbour uses.
 *
 * Pro: both replace walking the file browser down to the folder the notebook
 * already runs in, and neither needs a running kernel.
 * Con: JupyterLab serves the file browser on ``/lab/tree/`` instead, so the
 * link answers with a redirect, or a 404 on a server without Jupyter Notebook.
 */
/**
 * Wrap the active cell's lines, or let them run off to the right.
 *
 * The wrap is one class on the cell and two CSS rules, not an editor option,
 * so it reaches a cell the notebook has not built an editor for yet and it
 * survives the windowing that detaches a cell and attaches it again.
 * CodeMirror reads the computed ``white-space`` of its own content, so it
 * measures the wrapped lines the way it measures the option's.
 *
 * Pro: an ``%%ask`` question reads as a paragraph, which is what it is.
 * Con: the class is painted by the notebook watcher, so a cell in a document
 * no watcher runs over keeps the wrap its default asks for.
 */
const wrapPlugin = flagPlugin('yukti:wrap-cell', {
  command: 'yukti:toggle-wrap',
  read: wrapsLines,
  write: (cell, on) => {
    if (on === wrapsByDefault(cell)) {
      cell.sharedModel.deleteMetadata(WRAP_KEY);
    } else {
      cell.sharedModel.setMetadata(WRAP_KEY, on);
    }
  },
  icon: wrapped => (wrapped ? WRAPS.on : WRAPS.off),
  label: wrapped => (wrapped ? 'Stop Wrapping Lines' : 'Wrap Lines'),
  caption: wrapped =>
    wrapped
      ? 'Let this cell scroll sideways instead of wrapping its lines'
      : 'Wrap this cell\'s lines at the ask line length'
});

/**
 * The column an ``%%ask`` cell wraps its lines at.
 *
 * The length reaches the stylesheet as one CSS variable on the document, so
 * one write repaints every wrapped ask cell of every open notebook and no cell
 * has to be visited. ``ch`` is the width of a zero in the cell's own font,
 * which is the code font, so the number reads as a column count. A second
 * variable carries the colour of the dashed rule that marks the column, and
 * turns it transparent while no column is set.
 *
 * Pro: one setting, one place, and the reader sees the new width as they drag
 * the number.
 * Con: ``ch`` is exact for a monospaced font only, so a proportional code font
 * wraps a little short of the number.
 */
const askWrapPlugin: JupyterFrontEndPlugin<void> = {
  id: ASK_WRAP_PLUGIN_ID,
  autoStart: true,
  requires: [ISettingRegistry],
  activate: async (_app, registry: ISettingRegistry) => {
    const settings = await registry.load(ASK_WRAP_PLUGIN_ID);
    const apply = (): void => {
      const length = settings.get('lineLength').composite;
      const column = typeof length === 'number' && length > 0;
      const root = document.documentElement.style;
      root.setProperty(WIDTH_VAR, column ? `${length as number}ch` : 'none');
      root.setProperty(RULE_VAR, column ? RULE_COLOR : 'transparent');
    };
    apply();
    settings.changed.connect(apply);
  }
};

const fileMenuPlugin: JupyterFrontEndPlugin<void> = {
  id: 'yukti:file-menu',
  autoStart: true,
  requires: [INotebookTracker],
  activate: (app, tracker: INotebookTracker) => {
    app.commands.addCommand('yukti:open-in-cwd', {
      label: 'Open in CWD',
      caption: "Open this notebook's folder in a new browser tab",
      isEnabled: () => tracker.currentWidget != null,
      execute: () => {
        const dir = notebookDir(tracker.currentWidget);
        if (dir == null) {
          return;
        }
        const base = PageConfig.getBaseUrl();
        window.open(URLExt.join(base, 'tree', URLExt.encodeParts(dir)), '_blank');
      }
    });

    app.commands.addCommand('yukti:new-notebook-here', {
      label: 'New Notebook Here',
      caption: "Create a notebook beside this one, in the same folder",
      isEnabled: () => tracker.currentWidget != null,
      execute: async () => {
        const panel = tracker.currentWidget;
        const dir = notebookDir(panel);
        if (panel == null || dir == null) {
          return;
        }
        const created = await app.serviceManager.contents.newUntitled({
          path: dir,
          type: 'notebook'
        });
        const kernel = panel.sessionContext.session?.kernel?.name;
        await app.commands.execute('docmanager:open', {
          path: created.path,
          factory: 'Notebook',
          kernel: kernel == null ? undefined : { name: kernel }
        });
      }
    });
  }
};

/**
 * The plugin id, which is also the name ``schema/bookmarks.json`` carries, so
 * the settings registry hands this plugin that schema.
 */
const BOOKMARKS_PLUGIN_ID = 'yukti:bookmarks';
const BOOKMARK_COMMAND = 'yukti:toggle-bookmark';

/**
 * The Bookmarks tab and its two buttons per row.
 *
 * The rules copy the file listing's own spacing and lean on theme variables
 * only, so the tab reads as one more section of the tree page in the light and
 * in the dark theme. The remove button stays invisible until the row is
 * hovered or focused, so a still list shows paths and nothing else, and a
 * keyboard reaches the button all the same.
 */
const BOOKMARK_STYLE = `
.yukti-Bookmarks {
  overflow: auto;
  background: var(--jp-layout-color1);
}
.yukti-Bookmarks-empty {
  color: var(--jp-ui-font-color2);
  font-size: var(--jp-ui-font-size1);
  margin: 0;
  padding: 12px 16px;
}
.yukti-Bookmarks-list {
  list-style: none;
  margin: 0;
  padding: 4px 0;
}
.yukti-Bookmark {
  display: flex;
  align-items: center;
}
.yukti-Bookmark:hover {
  background: var(--jp-layout-color2);
}
.yukti-Bookmark button {
  background: none;
  border: none;
  color: var(--jp-ui-font-color1);
  cursor: pointer;
  font-family: var(--jp-ui-font-family);
  font-size: var(--jp-ui-font-size1);
}
.yukti-Bookmark-open {
  align-items: center;
  display: flex;
  flex: 1 1 auto;
  gap: 6px;
  min-width: 0;
  padding: 4px 8px;
  text-align: left;
}
.yukti-Bookmark-icon {
  display: flex;
  flex: 0 0 auto;
}
.yukti-Bookmark-name {
  flex: 0 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.yukti-Bookmark-dir {
  color: var(--jp-ui-font-color2);
  flex: 1 1 auto;
  font-size: var(--jp-ui-font-size0);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.yukti-Bookmark-drop {
  flex: 0 0 auto;
  font-size: var(--jp-ui-font-size2);
  line-height: 1;
  opacity: 0;
  padding: 2px 10px;
}
.yukti-Bookmark:hover .yukti-Bookmark-drop,
.yukti-Bookmark-drop:focus {
  opacity: 1;
}
`;

/**
 * One star, filled while the path is bookmarked.
 *
 * The glyph repeats the cell buttons' 16 px box and their ``jp-icon3`` class,
 * so the theme paints every yukti button the same grey.
 */
function starIcon(filled: boolean): LabIcon {
  const path =
    'M8.0 1.5 9.8 6.0 14.6 6.3 10.9 9.3 12.1 14.0 8.0 11.4 3.9 14.0 ' +
    '5.1 9.3 1.4 6.3 6.2 6.0Z';
  return new LabIcon({
    name: `yukti:bookmark-${filled ? 'on' : 'off'}`,
    svgstr:
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">' +
      `<path d="${path}" class="jp-icon3" ` +
      (filled
        ? 'fill="#616161"'
        : 'style="fill:none" stroke="#616161" stroke-width="1.3" ' +
          'stroke-linejoin="round"') +
      '/></svg>'
  });
}

// Both stars are built once, because ``LabIcon`` keys its instances by name
// and warns about a second icon under a name it already holds.
const STARS = { on: starIcon(true), off: starIcon(false) };

/**
 * The name and the folder a bookmark shows, and the glyph in front of them.
 *
 * A stored path that ends in a slash is a folder, which is the shape
 * ``filebrowser:open-path`` already reads: it strips the slash and refuses a
 * path that is no longer a directory. So the same one character carries the
 * type here and gets checked there, and the setting stays a list of plain
 * strings a reader can edit by hand.
 */
function bookmarkParts(path: string): {
  name: string;
  dir: string;
  icon: LabIcon;
} {
  const isDir = path.endsWith('/');
  const clean = isDir ? path.slice(0, -1) : path;
  const icon = isDir
    ? folderIcon
    : clean.endsWith('.ipynb')
      ? notebookIcon
      : fileIcon;
  return {
    name: PathExt.basename(clean) || '/',
    dir: PathExt.dirname(clean),
    icon
  };
}

/**
 * One bookmark: a wide button that opens it, and a narrow one that drops it.
 */
function bookmarkRow(
  path: string,
  open: (path: string) => void,
  drop: (path: string) => void
): HTMLLIElement {
  const { name, dir, icon } = bookmarkParts(path);
  const row = document.createElement('li');
  row.className = 'yukti-Bookmark';

  const go = document.createElement('button');
  go.className = 'yukti-Bookmark-open';
  go.title = path;
  const glyph = document.createElement('span');
  glyph.className = 'yukti-Bookmark-icon';
  icon.element({ container: glyph, width: '16px', height: '16px' });
  const label = document.createElement('span');
  label.className = 'yukti-Bookmark-name';
  label.textContent = name;
  const parent = document.createElement('span');
  parent.className = 'yukti-Bookmark-dir';
  parent.textContent = dir;
  go.append(glyph, label, parent);
  go.addEventListener('click', () => open(path));

  const remove = document.createElement('button');
  remove.className = 'yukti-Bookmark-drop';
  remove.title = 'Remove this bookmark';
  remove.setAttribute('aria-label', `Remove the bookmark for ${path}`);
  remove.textContent = '×';
  remove.addEventListener('click', () => drop(path));

  row.append(go, remove);
  return row;
}

/**
 * The Bookmarks tab, and the call that fills it from a list of paths.
 *
 * ``draw`` rebuilds every row, because the list is a handful of items and a
 * rebuild costs less than the code that would patch it in place.
 */
function bookmarkList(
  open: (path: string) => void,
  drop: (path: string) => void
): { widget: Widget; draw: (paths: string[]) => void } {
  const widget = new Widget();
  widget.addClass('yukti-Bookmarks');
  const draw = (paths: string[]): void => {
    widget.node.replaceChildren();
    if (paths.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'yukti-Bookmarks-empty';
      empty.textContent =
        'No bookmarks yet. Right-click a file or a folder under Files, ' +
        'then choose Bookmark.';
      widget.node.appendChild(empty);
      return;
    }
    const list = document.createElement('ul');
    list.className = 'yukti-Bookmarks-list';
    for (const path of paths) {
      list.appendChild(bookmarkRow(path, open, drop));
    }
    widget.node.appendChild(list);
  };
  return { widget, draw };
}

/**
 * A Bookmarks tab beside Files and Running on the ``/tree/`` page.
 *
 * The tab is one more widget in the tab panel Jupyter Notebook builds for that
 * page and hands out as ``INotebookTree``, so it is added the way the Running
 * tab is added, and it inherits the panel's own look and keyboard handling.
 * Clicking a row calls ``filebrowser:open-path``, which changes the folder for
 * a directory and opens the document for a file, so one call covers both and
 * reports the path itself when it is gone.
 *
 * The paths live in this plugin's settings, so they survive a restart, reach
 * every browser the same server serves, and stay editable by hand under
 * Settings, Advanced Settings Editor.
 *
 * Pro: a deep working folder and a notebook opened every morning are each one
 * click from the page the server already opens on.
 * Con: ``INotebookTree`` is a Jupyter Notebook token, so in JupyterLab it
 * arrives as ``null`` and the whole plugin stops, tab, command and all. The
 * toolbar star also reads the selection, and no signal reports a selection
 * made by keyboard, so after arrow keys the star can name the wrong state
 * until the next click. The menu entry reads the selection when it opens, so
 * it is always right.
 */
const bookmarksPlugin: JupyterFrontEndPlugin<void> = {
  id: BOOKMARKS_PLUGIN_ID,
  autoStart: true,
  requires: [IDefaultFileBrowser, ISettingRegistry],
  optional: [INotebookTree],
  activate: async (
    app,
    browser: IDefaultFileBrowser,
    registry: ISettingRegistry,
    tree: INotebookTree | null
  ) => {
    if (tree == null) {
      return;
    }
    const style = document.createElement('style');
    style.textContent = BOOKMARK_STYLE;
    document.head.appendChild(style);

    const settings = await registry.load(BOOKMARKS_PLUGIN_ID);
    const read = (): string[] =>
      (settings.get('bookmarks').composite as string[] | undefined) ?? [];
    const write = (paths: string[]): Promise<void> =>
      settings.set('bookmarks', paths);

    const { commands } = app;

    /**
     * The path the command acts on: the first selected item, or, with nothing
     * selected, the folder the file browser shows.
     */
    const target = (): string => {
      const [item] = Array.from(browser.selectedItems());
      if (item == null) {
        return `${browser.model.path}/`;
      }
      return item.type === 'directory' ? `${item.path}/` : item.path;
    };

    const open = async (path: string): Promise<void> => {
      await commands.execute('filebrowser:open-path', { path });
      // A folder changed the file browser, which sits under another tab, so
      // the Files tab is raised to show what the click did. A file opened a
      // page of its own, so this tab stays where it is.
      if (path.endsWith('/') && commands.hasCommand('filebrowser:activate')) {
        await commands.execute('filebrowser:activate');
      }
    };

    const { widget, draw } = bookmarkList(
      path => void open(path),
      path => void write(read().filter(kept => kept !== path))
    );
    widget.id = 'yukti-bookmarks-tree';
    widget.title.label = 'Bookmarks';
    widget.title.icon = STARS.on;
    widget.node.setAttribute('role', 'region');
    widget.node.setAttribute('aria-label', 'Bookmarks Section');
    tree.addWidget(widget);
    tree.tabBar.addTab(widget.title);

    draw(read());
    settings.changed.connect(() => {
      draw(read());
      commands.notifyCommandChanged(BOOKMARK_COMMAND);
    });

    const marked = (): boolean => read().includes(target());
    commands.addCommand(BOOKMARK_COMMAND, {
      label: () => (marked() ? 'Remove Bookmark' : 'Bookmark'),
      caption: () =>
        marked()
          ? 'Drop this path from the Bookmarks tab'
          : 'Pin this path to the Bookmarks tab',
      icon: () => (marked() ? STARS.on : STARS.off),
      isToggled: marked,
      execute: async () => {
        const path = target();
        const paths = read();
        await write(
          paths.includes(path)
            ? paths.filter(kept => kept !== path)
            : [...paths, path]
        );
      }
    });

    // The star names the selection, and the selection changes on a click and
    // on every folder change, so the button is told to read itself again.
    browser.model.pathChanged.connect(() => {
      commands.notifyCommandChanged(BOOKMARK_COMMAND);
    });
    browser.node.addEventListener('click', () => {
      commands.notifyCommandChanged(BOOKMARK_COMMAND);
    });
  }
};

export default [
  plugin,
  aiModePlugin,
  plainTextPlugin,
  skipAskPlugin,
  editOutputPlugin,
  copyPlugin,
  freezePlugin,
  hidePlugin,
  wrapPlugin,
  askWrapPlugin,
  fileMenuPlugin,
  bookmarksPlugin
];
