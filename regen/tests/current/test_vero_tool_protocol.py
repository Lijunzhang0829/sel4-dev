"""Large feedback must remain readable without duplicating native log files."""
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from regen.tools.vero_application_tool import argument_parser, read_json_frame
from regen.tools.vero_tool_protocol import (
    DIAGNOSTIC_PREVIEW_BYTES, MAX_DIAGNOSTIC_CHUNK_BYTES,
    preview_diagnostic, read_diagnostic_chunk,
)


class VeroToolProtocolTests(unittest.TestCase):
    def test_socket_frame_larger_than_old_16_mib_limit(self):
        payload = b'{"diagnostic":"' + b'x' * (17 * 1024 * 1024) + b'"}\n'
        reader, writer = socket.socketpair()
        errors = []
        def send():
            try:
                # Exercise partial network writes, not only an in-memory read.
                for start in range(0, len(payload), 65521):
                    writer.sendall(payload[start:start + 65521])
            except Exception as error:
                errors.append(error)
            finally:
                writer.close()
        thread = threading.Thread(target=send)
        thread.start()
        try:
            with reader, reader.makefile('rb') as stream:
                result = read_json_frame(stream)
            self.assertEqual(len(result['diagnostic']), 17 * 1024 * 1024)
        finally:
            reader.close()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)

    def test_incomplete_and_oversize_frames_fail_before_json_parse(self):
        for frame in (b'', b'{"diagnostic":"half', b'{"valid":true}'):
            with self.subTest(frame=frame), self.assertRaisesRegex(ValueError, 'incomplete'):
                read_json_frame(io.BytesIO(frame))
        with self.assertRaisesRegex(ValueError, 'exceeds 16 bytes'):
            read_json_frame(io.BytesIO(b'{"diagnostic":"' + b'x' * 30), max_bytes=16)
        with self.assertRaisesRegex(ValueError, 'invalid VERO JSON'):
            read_json_frame(io.BytesIO(b'{broken}\n'))
        with self.assertRaisesRegex(ValueError, 'expected a JSON object'):
            read_json_frame(io.BytesIO(b'[]\n'))

    def test_large_sparse_log_reads_only_head_and_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'native.log'
            total = 800 * 1024 * 1024
            with path.open('wb') as stream:
                stream.write(b'FIRST native diagnostic\n')
                stream.seek(total - 28)
                stream.write(b'LAST native failure details\n!')
            actual = path.stat().st_size
            text, metadata = preview_diagnostic(path)
            self.assertTrue(text.startswith('FIRST native diagnostic\n'))
            self.assertTrue(text.endswith('LAST native failure details\n!'))
            self.assertIn('bytes omitted', text)
            self.assertTrue(metadata['truncated'])
            self.assertEqual(metadata['total_bytes'], actual)
            self.assertEqual(metadata['head_bytes'] + metadata['tail_bytes'], DIAGNOSTIC_PREVIEW_BYTES)
            self.assertLess(len(text), DIAGNOSTIC_PREVIEW_BYTES + 200)
            self.assertLess(len(json.dumps({'diagnostic':text})), 400000)
            self.assertEqual(path.stat().st_size, actual)

    def test_small_and_empty_logs_are_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'native.log'
            for content in ('', '小日志\n✔ done\n'):
                path.write_text(content, encoding='utf-8')
                text, metadata = preview_diagnostic(path)
                self.assertEqual(text, content)
                self.assertFalse(metadata['truncated'])
                self.assertEqual(metadata['next_offset'], len(content.encode('utf-8')))
                self.assertTrue(read_diagnostic_chunk(path, offset=metadata['total_bytes'])['eof'])

    def test_pages_reconstruct_multibyte_text_at_every_small_limit(self):
        content = ('a中é🦊\n' * 19) + 'END'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'native.log'
            path.write_text(content, encoding='utf-8')
            for limit in range(1, 18):
                with self.subTest(limit=limit):
                    offset, pieces = 0, []
                    while True:
                        page = read_diagnostic_chunk(path, offset, limit)
                        pieces.append(page['text'])
                        self.assertLessEqual(page['returned_bytes'], limit + 3)
                        self.assertEqual(page['offset'], offset)
                        if page['eof']:
                            break
                        self.assertGreater(page['next_offset'], offset)
                        offset = page['next_offset']
                    self.assertEqual(''.join(pieces), content)
                    self.assertEqual(page['next_offset'], len(content.encode('utf-8')))

    def test_invalid_bytes_and_terminal_partial_utf8_do_not_stall(self):
        data = b'a\xffb\xe4\xb8'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'native.log'
            path.write_bytes(data)
            offset, pieces = 0, []
            for _ in range(10):
                page = read_diagnostic_chunk(path, offset, 1)
                pieces.append(page['text'])
                offset = page['next_offset']
                if page['eof']:
                    break
            self.assertTrue(page['eof'])
            self.assertEqual(''.join(pieces), data.decode('utf-8', errors='replace'))

    def test_chunk_parameters_are_bounded_before_file_open(self):
        for offset, limit in ((-1, 1), (True, 1), (0, 0), (0, True),
                              (0, 1.5), (0, MAX_DIAGNOSTIC_CHUNK_BYTES + 1)):
            with self.subTest(offset=offset, limit=limit), self.assertRaises(ValueError):
                read_diagnostic_chunk('/does/not/exist', offset, limit)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'native.log'
            path.write_bytes(b'one')
            with self.assertRaisesRegex(ValueError, 'exceeds diagnostic size'):
                read_diagnostic_chunk(path, 4)

    def test_diagnostic_command_exposes_byte_pagination(self):
        args = vars(argument_parser().parse_args([
            'diagnostic', 'abc123', 'Vero_A', '--offset', '123', '--limit', '456']))
        self.assertEqual(args, {'cmd':'diagnostic', 'request_id':'abc123',
            'module':'Vero_A', 'offset':123, 'limit':456})

    def test_log_symlink_and_fifo_are_rejected_without_open_race_or_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / 'original.log'
            original.write_bytes(b'private log')
            linked = root / 'linked.log'
            linked.symlink_to(original)
            fifo = root / 'pipe.log'
            os.mkfifo(fifo)
            for function in (preview_diagnostic, read_diagnostic_chunk):
                with self.subTest(function=function.__name__, kind='symlink'):
                    with self.assertRaises(OSError):
                        function(linked)
                with self.subTest(function=function.__name__, kind='fifo'):
                    with self.assertRaisesRegex(ValueError, 'regular file'):
                        function(fifo)


if __name__ == '__main__':
    unittest.main()
