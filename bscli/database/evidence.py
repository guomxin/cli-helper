"""Human-readable, authenticated source links and transport-sized evidence pages."""
import hashlib
import json
from urllib.parse import quote

from bscli.database.content import plain_text


def text_revision(content):
    return hashlib.sha256(plain_text(content).encode('utf-8')).hexdigest()


def source_reference(source_id, row, base_url=''):
    log_id = row.get('work_log_id', row['id'])
    content = row.get('log_content', row.get('content', ''))
    revision = text_revision(content)
    path = f'/database/logs/{quote(source_id, safe="")}/{log_id}'
    return {'source_url': base_url.rstrip('/') + path + '?revision=' + revision if base_url else None,
            'source_label': f"{row.get('fullname') or row.get('username') or '未署名'} · {row.get('log_date', '')} 日志",
            'source_revision_hash': revision}


def prepare_evidence_page(result, plan, arguments, base_url=''):
    """Trim by serialized character budget; retain complete pagination metadata."""
    rows = result['rows']
    texts = {str(row['id']): plain_text(row.get('content', '')) for row in rows}
    for row in rows:
        row.update(source_reference(result['source_id'], row, base_url))
        if arguments.get('expected_revision') and arguments['expected_revision'] != row['source_revision_hash']:
            from bscli.database.independent import DatabaseRejected
            raise DatabaseRejected('DATABASE_CONTENT_CHANGED')
        if plan.evidence_format == 'compact':
            # Paragraphs carry the text once; raw/full text remains in the source viewer.
            if plan.mode:
                row.pop('content', None)
            row.pop('plain_text', None)
            row.pop('matches', None)
            if plan.comments:
                row.pop('log_content', None)  # log_plain_text remains for comment context.
    if 'analysis' in result:
        result['analysis']['instructions'].extend([
            '先逐条检查日志内的独立事项，再合并同一事项。输出前逐条对照 coverage_manifest；未覆盖事项须补充，主动省略要说明理由。',
            '用户来源显示 source_label 的查看原文链接；可在 source_url 后加 #paragraph-N 定位日志段落。保留内部 evidence_id，但不要向用户堆积系统编号；评论证据链接只定位关联日志，不冒充评论原文。',
            'content_complete=false 时，以 log_id、同一 mode、next_text_offset 作为 text_offset、source_revision_hash 作为 expected_revision 续读，直到 next_text_offset=null；合并连续片段后再读下一页。不能宣称部分正文已完整总结。',
        ])
    def manifest():
        if 'analysis' in result:
            result['analysis']['coverage_manifest'] = [
                {'evidence_id': row['evidence_id'], 'source_label': row['source_label'],
                 'paragraph_count': len(row.get('passages', [])),
                 'content_complete': row.get('content_complete', True)} for row in rows]
    def size():
        manifest()
        return len(json.dumps(result, ensure_ascii=False, default=str, indent=2).encode('utf-16-le')) // 2
    def fragment(row, offset, length):
        text = texts[str(row['id'])]
        end = min(len(text), offset + length)
        cursor = 0
        passages = []
        for i, line in enumerate(text.split('\n'), 1):
            lo, hi = max(offset, cursor), min(end, cursor + len(line))
            if hi > lo and line[lo-cursor:hi-cursor].strip():
                passages.append({'evidence_id': row['evidence_id'] + f':paragraph:{i}', 'text': line[lo-cursor:hi-cursor]})
            cursor += len(line) + 1
        row['passages'] = passages
        row.pop('content', None)
        row.pop('plain_text', None)
        row['text_offset'] = offset
        row['next_text_offset'] = end if end < len(text) else None
        row['content_complete'] = offset == 0 and end == len(text)
        row['total_text_chars'] = len(text)
    if 'text_offset' in arguments and rows:
        offset = arguments['text_offset']
        if offset > len(texts[str(rows[0]['id'])]):
            from bscli.database.independent import DatabaseRejected
            raise DatabaseRejected('INVALID_DATABASE_ARGUMENTS')
        fragment(rows[0], offset, 2000)
    def metadata():
        result['returned'] = len(rows)
        if rows and result['has_more']:
            last = rows[-1]
            cursor = {'date': str(last[plan.cursor_column]), 'id': str(last['id']), 'scope': plan.scope}
            if plan.include_descendants:
                cursor['department_scope'] = hashlib.sha256(json.dumps(result['resolved_department_scope'], sort_keys=True).encode()).hexdigest()
            result['next_cursor'] = cursor
        result['content_complete'] = all(row.get('content_complete', True) for row in rows)
        result['columns'] = list(rows[0]) if rows else result['columns']
    metadata()
    while len(rows) > 1 and size() > plan.max_chars:
        rows.pop()
        result['has_more'] = result['truncated'] = True
        metadata()
    if rows and size() > plan.max_chars and not plan.comments:
        length = min(2000, len(texts[str(rows[0]['id'])]))
        while length > 0:
            fragment(rows[0], arguments.get('text_offset', 0), length)
            metadata()
            if size() <= plan.max_chars:
                break
            length //= 2
    if size() > plan.max_chars:
        from bscli.database.independent import DatabaseRejected
        raise DatabaseRejected('DATABASE_EVIDENCE_TOO_LARGE')
    return result
