"""Read-only inspection of the exact in-memory V3 parser source around line 116."""
import inspect
import json
import textwrap
from pathlib import Path
import keiba_jra_result_fetch as original

OUT = Path('keiba_nakayama8r_line116_inspect.json')

def main():
    report = {'posting': False, 'source_files_modified': False, 'target_line': 116}
    try:
        source = textwrap.dedent(inspect.getsource(original.extract))
        old_field = '    field = len(rows)'
        old_duplicate = "    if len({\n        row['number'] for row in rows\n    }) != field:"
        if source.count(old_field) != 1 or source.count(old_duplicate) != 1:
            raise ValueError('Original extract source differs from expected V3 source; no assumptions made')
        source = source.replace(old_field, '    field = 15', 1)
        source = source.replace(old_duplicate,
            "    if len({\n        row['number'] for row in rows\n    }) != len(rows):", 1)
        lines = source.splitlines()
        report['total_lines'] = len(lines)
        report['nearby_lines'] = [{'line': i, 'code': lines[i - 1]}
                                  for i in range(101, min(131, len(lines) + 1))]
        print('V3 IN-MEMORY SOURCE LINE 101-130 (NO EXECUTION):')
        for item in report['nearby_lines']:
            print(f"{item['line']:>4}: {item['code']}")
        report['status'] = 'SOURCE_INSPECTED'
    except Exception as exc:
        report['status'] = 'ERROR'
        report['error'] = f'{type(exc).__name__}: {exc}'
        print(report['error'])
    finally:
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('NO POSTING / NO MODIFICATION /', OUT)

if __name__ == '__main__':
    main()
