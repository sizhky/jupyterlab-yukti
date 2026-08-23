import type { JupyterFrontEndPlugin } from '@jupyterlab/application';
import type { ICellModel } from '@jupyterlab/cells';
import { IEditorServices, type IEditorMimeTypeService } from '@jupyterlab/codeeditor';
import {
  type ICell,
  type IOutput,
  isCode,
  isDisplayData,
  isError,
  isExecuteResult,
  isStream
} from '@jupyterlab/nbformat';
import {
  INotebookTracker,
  NotebookActions,
  type NotebookPanel
} from '@jupyterlab/notebook';

const COMM_TARGET = 'yukti.notebook_prefix';
const PLAIN_MIME_TYPE = 'text/plain';

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

const plugin: JupyterFrontEndPlugin<void> = {
  id: 'yukti:notebook-prefix',
  autoStart: true,
  requires: [INotebookTracker],
  activate: (_app, tracker: INotebookTracker) => {
    NotebookActions.executionScheduled.connect((_sender, { cell, notebook }) => {
      const source = text(cell.model.toJSON().source);
      if (!isAskSource(source)) {
        return;
      }

      const index = notebook.widgets.indexOf(cell);
      const panel = tracker.find(candidate => candidate.content === notebook);
      const kernel = panel?.sessionContext.session?.kernel;
      if (index < 0 || kernel == null) {
        return;
      }

      const cells = notebook.widgets
        .slice(0, index)
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
              typeof candidate.source === 'string'
            ) ? {
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
        if (data.type !== 'replace_cells' || !Array.isArray(data.cells)) {
          return;
        }

        const prefix = new Map(
          notebook.widgets.slice(0, currentIndex).map(widget => [widget.model.id, widget])
        );
        const edits = data.cells.map(value => {
          if (typeof value !== 'object' || value == null || Array.isArray(value)) {
            return null;
          }
          const replacement = value as Record<string, unknown>;
          const widget = prefix.get(replacement.cell_id as string);
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
        cells
      });
    });
  }
};

/**
 * Drop Python highlighting from every ``%%ask`` cell.
 *
 * A cell editor takes its language from ``cell.model.mimeType``, so
 * ``text/plain`` leaves the prompt unhighlighted. The notebook resets that
 * mimetype when a cell arrives and when ``language_info`` lands, so both paths
 * re-sync.
 *
 * Pro: one prefix test per keystroke, and the mimetype setter ignores
 * same-value writes, so CodeMirror reloads its language only when the cell
 * crosses the ``%%ask`` boundary.
 * Con: the mimetype is also what completion and tooltips read, so an ``%%ask``
 * cell loses Python completions too.
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

  const watch = (cell: ICellModel): void => {
    sync(cell);
    cell.contentChanged.connect(sync);
  };

  for (const cell of model.cells) {
    watch(cell);
  }
  model.cells.changed.connect((_, change) => {
    if (change.type === 'add' || change.type === 'set') {
      change.newValues.forEach(watch);
    }
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
    tracker.widgetAdded.connect((_sender, panel) => {
      void panel.context.ready.then(() => {
        if (!panel.isDisposed) {
          trackAskCells(panel, editorServices.mimeTypeService);
        }
      });
    });
  }
};

export default [plugin, plainTextPlugin];
