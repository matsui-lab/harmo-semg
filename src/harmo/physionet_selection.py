"""Bounded WFDB selection, independent of dataset identity and channel meaning."""
from pathlib import PurePosixPath
import urllib.parse as up


def select_records(base, stage, transport, maximum, max_pages=100, max_index_bytes=8*1024**2):
    from harmo.public_batch import Links, safe_relative, write_json
    if type(maximum) is not int or maximum < 1:
        raise ValueError('positive_record_limit_required')
    selected, visited, documents = [], [], []
    pending = ['']
    index_used = None
    while pending and len(selected) < maximum:
        if len(visited) >= max_pages:
            break
        relative = pending.pop(0)
        if relative in visited:
            continue
        visited.append(relative)
        path = stage / ('selection-listing-%04d.html' % len(visited))
        transport.fetch(base + up.quote(relative, safe='/'), path, 8*1024**2)
        parser = Links(); parser.feed(path.read_text())
        children = set()
        for href in parser.links:
            if href.startswith(('/', '?', '#')) or href == '../' or up.urlsplit(href).scheme or up.urlsplit(href).netloc:
                continue
            name = up.unquote(href)
            safe_relative(name)
            if '/' in name.rstrip('/') or '?' in name or '#' in name:
                raise ValueError('unexpected_directory_entry')
            children.add(name)
        if not relative:
            documents = sorted(n for n in children if not n.endswith('/') and
                               (PurePosixPath(n).suffix.lower() in ('.md', '.csv', '.json') or
                                n.lower() in ('readme.txt', 'license.txt')))
        # A repository index is one bounded request, not recursive file enumeration.
        if 'RECORDS' in children:
            index = stage / ('selection-index-%04d.txt' % len(visited))
            transport.fetch(base + up.quote(relative + 'RECORDS', safe='/'), index, max_index_bytes)
            index_used = str(index)
            with index.open() as stream:
                for line in stream:
                    name = line.strip()
                    if not name or name.startswith('#'):
                        continue
                    safe_relative(name)
                    if name.endswith('/') or any(c.isspace() for c in name) or '?' in name or '#' in name:
                        raise ValueError('invalid_records_index_entry')
                    header = relative + (name if name.endswith('.hea') else name + '.hea')
                    if header not in selected:
                        selected.append(header)
                    if len(selected) == maximum:
                        break
            # Index is authoritative for this subtree. Do not recurse into it.
            continue
        for name in sorted(children):
            if name.endswith('.hea'):
                selected.append(relative + name)
                if len(selected) == maximum:
                    break
        # Depth-first lexical traversal stops at the requested number of headers.
        pending[0:0] = [relative + n for n in sorted(children) if n.endswith('/')]
    result = dict(version='physionet-selection/1', records=selected, documents=documents,
                  method='bounded-index-or-depth-first', index_path=index_used,
                  visited_directories=visited, full_inventory=False,
                  available_records=None, selection_complete=False,
                  stopped='record_limit' if len(selected) >= maximum else ('page_limit' if pending else 'source_exhausted'),
                  limits=dict(records=maximum, directory_pages=max_pages, index_bytes=max_index_bytes))
    write_json(stage / 'selection.json', result)
    return result


def acquire_selected(selection, base, stage, transport):
    """Read headers first; obtain actual dependencies, never guessed dat filenames."""
    from harmo.public_batch import inspect_header
    raw = stage / 'raw'
    assets, errors, fetched = [], [], set()
    def fetch(name, limit=256*1024**2):
        if name in fetched:
            return
        receipt = transport.fetch(base + up.quote(name, safe='/'), raw / name, limit)
        receipt['relative_path'] = name
        assets.append(receipt); fetched.add(name)
    for header in selection['records']:
        try:
            fetch(header, 1024**2)
            meta = inspect_header(raw / header, raw)
            for signal in meta['signals']:
                fetch(str(PurePosixPath(header).parent / signal['file']))
        except Exception as exc:
            errors.append(dict(file=header, error=str(exc)))
    # Root metadata is optional, bounded, and never delays the selected waveform.
    for name in selection['documents'][:20]:
        try:
            fetch(name, 2*1024**2)
        except Exception as exc:
            errors.append(dict(file=name, error=str(exc), scope='optional-document'))
    return assets, errors
