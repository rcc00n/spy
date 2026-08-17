"""Keep browser-extension failures out of noVNC's fatal error overlay.

Patch the packaged handler rather than replacing noVNC or hiding its own errors.
Run at image build time; rerunning is safe.
"""
import sys
from pathlib import Path

MARKER = '// Spy: ignore errors originating in browser extensions only.'
GUARD = '''        // Spy: ignore errors originating in browser extensions only.
        const extensionURL = /^(?:chrome-extension|moz-extension|safari-web-extension):\\/\\//;
        const filename = event && event.filename;
        const stack = err && typeof err.stack === 'string' ? err.stack : '';
        const firstFrame = stack.split('\\n').slice(1).find(line => line.trim()) || '';
        if ((filename && extensionURL.test(filename)) ||
            (!filename && /^\\s*(?:at\\s+(?:[^()]*\\()?|[^@]*@)?(?:chrome-extension|moz-extension|safari-web-extension):\\/\\//.test(firstFrame))) {
            return false;
        }
'''


def patch(root):
    handler = root / 'app/error-handler.js'
    source = handler.read_text()
    anchor = '    function handleError(event, err) {\n'
    if MARKER not in source:
        if source.count(anchor) != 1:
            raise RuntimeError('Unexpected noVNC error handler; inspect before patching')
        handler.write_text(source.replace(anchor, anchor + GUARD, 1))
    html = root / 'vnc.html'
    source = html.read_text()
    source = source.replace('src="app/error-handler.js"', 'src="app/error-handler.js?v=spy-extension-fix-1"')
    html.write_text(source)


if __name__ == '__main__':
