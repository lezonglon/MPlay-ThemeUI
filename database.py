import sqlite3
import os
import hashlib
import subprocess
import json
import re
import shutil

VIDEO_EXTENSIONS = {
    '.mkv', '.mp4', '.avi', '.mov', '.flv', '.webm', '.iso', '.wmv', '.m2ts', '.ts'
}
AUDIO_EXTENSIONS = {
    '.mp3', '.flac', '.m4a', '.wav', '.ogg', '.opus', '.wma'
}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.gif'}

class Database:
    def __init__(self, db_path="library.db"):
        self.db_path = db_path
        self.mpv_path = r"D:/Software/MPV_Lazy_Pack/mpv.exe"
        self.ffmpeg_path = shutil.which('ffmpeg')
        self.cache_dir = os.path.join(os.getcwd(), "cache_thumbs")
        
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
            
        self._init_db()

    def update_mpv_path(self, path):
        if os.path.exists(path):
            self.mpv_path = path
            possible_ffmpeg = path.replace('mpv.exe', 'ffmpeg.exe')
            if os.path.exists(possible_ffmpeg):
                self.ffmpeg_path = possible_ffmpeg

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                folder TEXT,
                cover_path TEXT, 
                last_played TIMESTAMP,
                progress INTEGER DEFAULT 0,
                duration INTEGER DEFAULT 0,
                is_favorite INTEGER DEFAULT 0,
                play_count INTEGER DEFAULT 0,
                listened_seconds REAL DEFAULT 0.0,
                like_score REAL DEFAULT 0.0,
                lyrics_text TEXT
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_videos_folder ON videos(folder)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_videos_path ON videos(path)')
        
        c.execute("PRAGMA table_info(videos)")
        columns = [column[1] for column in c.fetchall()]
        if 'is_favorite' not in columns:
            c.execute('ALTER TABLE videos ADD COLUMN is_favorite INTEGER DEFAULT 0')
        if 'play_count' not in columns:
            c.execute('ALTER TABLE videos ADD COLUMN play_count INTEGER DEFAULT 0')
        if 'listened_seconds' not in columns:
            c.execute('ALTER TABLE videos ADD COLUMN listened_seconds REAL DEFAULT 0.0')
        if 'like_score' not in columns:
            c.execute('ALTER TABLE videos ADD COLUMN like_score REAL DEFAULT 0.0')
        if 'lyrics_text' not in columns:
            c.execute('ALTER TABLE videos ADD COLUMN lyrics_text TEXT')

        conn.commit()
        conn.close()

        # 自動清理與補完缺少圖案的專輯封面
        self._auto_fill_missing_covers()

    def _extract_embedded_art(self, video_path, output_path):
        if not self.ffmpeg_path:
            self.ffmpeg_path = shutil.which('ffmpeg')
        if not self.ffmpeg_path:
            return False
        try:
            cmd1 = [
                self.ffmpeg_path, "-y", "-i", video_path, "-an",
                "-vcodec", "copy", "-f", "mjpeg", output_path
            ]
            res1 = subprocess.run(
                cmd1, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                timeout=5
            )
            if res1.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True

            cmd2 = [
                self.ffmpeg_path, "-y", "-i", video_path,
                "-vframes", "1", "-vf", "scale=400:-1", "-q:v", "5", output_path
            ]
            res2 = subprocess.run(
                cmd2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                timeout=5
            )
            if res2.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True
        except: pass
        return False

    def _generate_screenshot(self, video_path, output_path):
        try:
            if self.ffmpeg_path:
                cmd = [
                    self.ffmpeg_path, "-y", "-ss", "5", "-i", video_path,
                    "-vframes", "1", "-vf", "scale=400:-1", "-q:v", "5", output_path
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, timeout=10)
                if os.path.exists(output_path): return True

            if os.path.exists(self.mpv_path):
                cmd = [
                    self.mpv_path, video_path,
                    "--no-config", "--no-audio", "--no-sub", 
                    "--start=5", "--frames=1",
                    "--hwdec=no", 
                    "--vf=scale=400:-1,format=yuv420p", 
                    "--o=" + output_path
                ]
                subprocess.run(cmd, capture_output=True, timeout=15)
                if os.path.exists(output_path): return True
        except: pass
        return False

    def _generate_thumbnail(self, video_path):
        file_hash = hashlib.md5(video_path.encode('utf-8')).hexdigest()
        output_filename = f"{file_hash}.jpg"
        output_path = os.path.join(self.cache_dir, output_filename)

        if os.path.exists(output_path):
            return output_path.replace('\\', '/')

        if self._extract_embedded_art(video_path, output_path):
            return output_path.replace('\\', '/')

        if self._generate_screenshot(video_path, output_path):
            return output_path.replace('\\', '/')

        return None

    def _find_folder_cover(self, folder, audio_files=None):
        try:
            files = os.listdir(folder)
            for f in files:
                if f.lower() in ['cover.jpg', 'cover.png', 'folder.jpg', 'folder.png', 'front.jpg', 'front.png']:
                    return os.path.join(folder, f).replace('\\', '/')
            for f in files:
                if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                    return os.path.join(folder, f).replace('\\', '/')
        except: pass

        if audio_files:
            folder_hash = hashlib.md5(folder.encode('utf-8')).hexdigest()
            embedded_cover_path = os.path.join(self.cache_dir, f"album_{folder_hash}.jpg")
            if os.path.exists(embedded_cover_path) and os.path.getsize(embedded_cover_path) > 0:
                return embedded_cover_path.replace('\\', '/')
            
            for audio in audio_files:
                if self._extract_embedded_art(audio, embedded_cover_path):
                    return embedded_cover_path.replace('\\', '/')

        return None

    def _auto_fill_missing_covers(self):
        """為現有資料庫中沒有封面的專輯資料夾自動解析並補全內嵌封面"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute('SELECT folder, path FROM videos WHERE cover_path IS NULL OR cover_path = ""')
            rows = c.fetchall()
            
            folder_map = {}
            for r in rows:
                f = r['folder']
                if f not in folder_map: folder_map[f] = []
                folder_map[f].append(r['path'])
                
            updates = []
            for folder, paths in folder_map.items():
                cover = self._find_folder_cover(folder, paths)
                if cover:
                    updates.append((cover, folder))
                    
            if updates:
                c.executemany('UPDATE videos SET cover_path = ? WHERE folder = ?', updates)
                conn.commit()
                print(f"[Cover Fix] Updated embedded covers for {len(updates)} folders!")
            conn.close()
        except Exception as e:
            print(f"[Cover Fix Error] {e}")

    def _find_lyrics_text(self, track_path):
        base, _ = os.path.splitext(track_path)
        possible_lrcs = [base + '.lrc', base + '.txt']
        
        for lrc_file in possible_lrcs:
            if os.path.exists(lrc_file):
                try:
                    with open(lrc_file, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read(3000)
                        clean_text = re.sub(r'\[\d+:\d+.\d+\]', ' ', content)
                        return clean_text.strip()
                except: pass
        return ""

    def add_video(self, path):
        folder = os.path.dirname(path)
        filename = os.path.basename(path)
        cover_path = self._find_folder_cover(folder, [path])
        lyrics_text = self._find_lyrics_text(path)
        
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        try:
            c.execute('''
                INSERT OR IGNORE INTO videos (path, filename, folder, cover_path, lyrics_text) 
                VALUES (?, ?, ?, ?, ?)
            ''', (path, filename, folder, cover_path, lyrics_text))
            
            if cover_path:
                c.execute('UPDATE videos SET cover_path = ? WHERE path = ?', (cover_path, path))
            conn.commit()
        except Exception as e:
            print(f"[DB Error] {e}")
        finally:
            conn.close()

    def scan_directory(self, root_folder):
        count = 0
        print(f"[System] Scanning: {root_folder}")
        folder_audios = {}
        all_tracks = []

        for root, dirs, files in os.walk(root_folder):
            folder_path = root.replace('\\', '/')
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in VIDEO_EXTENSIONS or ext in AUDIO_EXTENSIONS:
                    full_path = os.path.join(root, file).replace('\\', '/')
                    if folder_path not in folder_audios:
                        folder_audios[folder_path] = []
                    folder_audios[folder_path].append(full_path)
                    all_tracks.append((full_path, file, folder_path))

        items_to_insert = []
        folder_covers = {}

        for folder_path, audios in folder_audios.items():
            folder_covers[folder_path] = self._find_folder_cover(folder_path, audios)

        for full_path, file, folder_path in all_tracks:
            cover_path = folder_covers.get(folder_path)
            lyrics_text = self._find_lyrics_text(full_path)
            items_to_insert.append((full_path, file, folder_path, cover_path, lyrics_text))
            count += 1

        if items_to_insert:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            try:
                c.executemany('''
                    INSERT OR IGNORE INTO videos (path, filename, folder, cover_path, lyrics_text)
                    VALUES (?, ?, ?, ?, ?)
                ''', items_to_insert)
                
                for full_path, file, folder_path, cover_path, lyrics_text in items_to_insert:
                    if cover_path:
                        c.execute('UPDATE videos SET cover_path = ? WHERE path = ?', (cover_path, full_path))
                    if lyrics_text:
                        c.execute('UPDATE videos SET lyrics_text = ? WHERE path = ?', (lyrics_text, full_path))
                        
                conn.commit()
            except Exception as e:
                print(f"[DB Scan Error] {e}")
            finally:
                conn.close()

        print(f"[System] Scan complete, {count} files indexed")
        return count

    def get_folders(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT folder, cover_path FROM videos GROUP BY folder ORDER BY folder ASC')
        rows = c.fetchall()
        conn.close()
        result = []
        for row in rows:
            folder_path = row['folder']
            folder_name = os.path.basename(folder_path)
            if not folder_name: folder_name = folder_path
            result.append({"path": folder_path, "name": folder_name, "cover": row['cover_path']})
        return result

    def search_library(self, query):
        if not query or not query.strip():
            return self.get_folders()

        q = f"%{query.strip()}%"
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT DISTINCT folder, cover_path FROM videos 
            WHERE filename LIKE ? OR folder LIKE ? OR lyrics_text LIKE ?
            ORDER BY folder ASC
        ''', (q, q, q))
        rows = c.fetchall()
        conn.close()

        result = []
        for row in rows:
            folder_path = row['folder']
            folder_name = os.path.basename(folder_path)
            if not folder_name: folder_name = folder_path
            result.append({"path": folder_path, "name": folder_name, "cover": row['cover_path']})
        return result

    def get_videos_in_folder(self, folder_path):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM videos WHERE folder = ? ORDER BY filename ASC', (folder_path,))
        rows = c.fetchall()
        conn.close()
        result = []
        for row in rows:
            result.append({
                "id": row["id"], 
                "path": row["path"], 
                "filename": row["filename"], 
                "cover_path": row["cover_path"],
                "is_favorite": row["is_favorite"] if "is_favorite" in row.keys() else 0,
                "play_count": row["play_count"] if "play_count" in row.keys() else 0,
                "like_score": row["like_score"] if "like_score" in row.keys() else 0.0,
                "lyrics_text": row["lyrics_text"] if "lyrics_text" in row.keys() else ""
            })
        return result

    def delete_track(self, track_path, delete_file=False):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('DELETE FROM videos WHERE path = ?', (track_path,))
        conn.commit()
        conn.close()

        if delete_file and os.path.exists(track_path):
            try:
                os.remove(track_path)
                print(f"[Delete] Removed file from disk: {track_path}")
            except Exception as e:
                print(f"[Delete Error] {e}")
        return True

    def delete_folder(self, folder_path, delete_files=False):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('DELETE FROM videos WHERE folder = ?', (folder_path,))
        conn.commit()
        conn.close()

        if delete_files and os.path.exists(folder_path):
            try:
                import shutil
                shutil.rmtree(folder_path, ignore_errors=True)
                print(f"[Delete] Removed folder from disk: {folder_path}")
            except Exception as e:
                print(f"[Delete Error] {e}")
        return True

    def get_all_videos(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM videos')
        rows = c.fetchall()
        conn.close()
        res = []
        for row in rows:
            res.append({
                "path": row["path"], 
                "filename": row["filename"], 
                "cover_path": row["cover_path"],
                "is_favorite": row["is_favorite"] if "is_favorite" in row.keys() else 0,
                "play_count": row["play_count"] if "play_count" in row.keys() else 0,
                "like_score": row["like_score"] if "like_score" in row.keys() else 0.0
            })
        return res

    @staticmethod
    def _calc_like_score(play_count, is_fav, listened_sec, duration):
        retention = (listened_sec / duration) if (duration and duration > 0) else 0.0
        retention = min(max(retention, 0.0), 1.0)
        
        score = (play_count * 20.0) + (150.0 if is_fav else 0.0) + (retention * 60.0)
        if retention >= 0.88:
            score += 40.0
        return round(score, 1)

    def toggle_favorite(self, path):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM videos WHERE path = ?', (path,))
        row = c.fetchone()
        if not row:
            conn.close()
            return False, 0
        
        cur_fav = row["is_favorite"] if "is_favorite" in row.keys() and row["is_favorite"] is not None else 0
        new_fav = 1 if cur_fav == 0 else 0
        
        play_cnt = row["play_count"] if "play_count" in row.keys() and row["play_count"] is not None else 0
        listened = row["listened_seconds"] if "listened_seconds" in row.keys() and row["listened_seconds"] is not None else 0
        dur = row["duration"] if "duration" in row.keys() and row["duration"] is not None else 0
        
        new_score = self._calc_like_score(play_cnt, new_fav, listened, dur)
        
        c.execute('UPDATE videos SET is_favorite = ?, like_score = ? WHERE path = ?', (new_fav, new_score, path))
        conn.commit()
        conn.close()
        return bool(new_fav), new_score

    def record_play(self, path, listened_sec=0, total_sec=0):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM videos WHERE path = ?', (path,))
        row = c.fetchone()
        if not row:
            self.add_video(path)
            c.execute('SELECT * FROM videos WHERE path = ?', (path,))
            row = c.fetchone()
            if not row:
                conn.close()
                return

        play_cnt = (row["play_count"] if "play_count" in row.keys() and row["play_count"] is not None else 0) + 1
        is_fav = row["is_favorite"] if "is_favorite" in row.keys() and row["is_favorite"] is not None else 0
        
        cur_listened = row["listened_seconds"] if "listened_seconds" in row.keys() and row["listened_seconds"] is not None else 0
        new_listened = max(cur_listened, float(listened_sec))
        
        cur_dur = row["duration"] if "duration" in row.keys() and row["duration"] is not None else 0
        dur = float(total_sec) if total_sec > 0 else float(cur_dur)
        
        new_score = self._calc_like_score(play_cnt, is_fav, new_listened, dur)
        
        c.execute('''
            UPDATE videos 
            SET play_count = ?, listened_seconds = ?, duration = ?, like_score = ?, last_played = CURRENT_TIMESTAMP
            WHERE path = ?
        ''', (play_cnt, new_listened, int(dur), new_score, path))
        conn.commit()
        conn.close()

    def get_favorites(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM videos WHERE is_favorite = 1 ORDER BY like_score DESC, filename ASC')
        rows = c.fetchall()
        conn.close()
        res = []
        for row in rows:
            res.append({
                "id": row["id"],
                "path": row["path"],
                "filename": row["filename"],
                "folder": row["folder"],
                "cover_path": row["cover_path"],
                "is_favorite": 1,
                "play_count": row["play_count"] if "play_count" in row.keys() else 0,
                "like_score": row["like_score"] if "like_score" in row.keys() else 0.0
            })
        return res

    def get_top10(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM videos ORDER BY like_score DESC, play_count DESC, id ASC LIMIT 10')
        rows = c.fetchall()
        conn.close()
        res = []
        for row in rows:
            res.append({
                "id": row["id"],
                "path": row["path"],
                "filename": row["filename"],
                "folder": row["folder"],
                "cover_path": row["cover_path"],
                "is_favorite": row["is_favorite"] if "is_favorite" in row.keys() else 0,
                "play_count": row["play_count"] if "play_count" in row.keys() else 0,
                "like_score": row["like_score"] if "like_score" in row.keys() else 0.0,
                "listened_seconds": row["listened_seconds"] if "listened_seconds" in row.keys() else 0.0,
                "duration": row["duration"] if "duration" in row.keys() else 0
            })
        return res

    def get_track_info(self, path):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM videos WHERE path = ?', (path,))
        row = c.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": row["id"],
            "path": row["path"],
            "filename": row["filename"],
            "folder": row["folder"],
            "cover_path": row["cover_path"],
            "is_favorite": row["is_favorite"] if "is_favorite" in row.keys() else 0,
            "play_count": row["play_count"] if "play_count" in row.keys() else 0,
            "like_score": row["like_score"] if "like_score" in row.keys() else 0.0,
            "listened_seconds": row["listened_seconds"] if "listened_seconds" in row.keys() else 0.0,
            "duration": row["duration"] if "duration" in row.keys() else 0
        }
