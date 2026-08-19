package com.arfipod.decksave;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ContentValues;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.text.InputType;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import com.jcraft.jsch.ChannelSftp;
import com.jcraft.jsch.JSch;
import com.jcraft.jsch.Session;
import com.jcraft.jsch.SftpATTRS;
import com.jcraft.jsch.SftpException;
import com.jcraft.jsch.UserInfo;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.io.OutputStream;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.Properties;
import java.util.Vector;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

public class MainActivity extends Activity {
    private static final int PICK_KEY = 1001;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private EditText host, port, user, password, passphrase;
    private TextView keyName, status;
    private CheckBox withBak;
    private Button keyButton, backupButton;
    private ProgressBar progress;
    private Uri keyUri;
    private SharedPreferences prefs;

    @Override public void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("deck_save", MODE_PRIVATE);
        buildUi();
        host.setText(prefs.getString("host", ""));
        port.setText(prefs.getString("port", "22"));
        user.setText(prefs.getString("user", "deck"));
    }

    private int dp(int n) { return Math.round(n * getResources().getDisplayMetrics().density); }
    private TextView label(String s, float size) {
        TextView v = new TextView(this); v.setText(s); v.setTextSize(size); v.setPadding(0, dp(5), 0, dp(5)); return v;
    }
    private EditText input(String hint, int type) {
        EditText v = new EditText(this); v.setHint(hint); v.setInputType(type); v.setSingleLine(true); return v;
    }

    private void buildUi() {
        ScrollView sv = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL); root.setPadding(dp(20), dp(20), dp(20), dp(30));
        sv.addView(root, new ScrollView.LayoutParams(-1, -2));
        TextView title = label("Elden Ring · Steam Deck backup", 24); title.setTypeface(null, 1); root.addView(title);
        root.addView(label("Descarga ER0000.sl2 por SSH/SFTP sin modificar la partida de la Deck.", 15));
        root.addView(label("Conexión SSH", 18));
        host = input("IP o nombre de la Deck", InputType.TYPE_CLASS_TEXT);
        port = input("Puerto", InputType.TYPE_CLASS_NUMBER);
        user = input("Usuario", InputType.TYPE_CLASS_TEXT);
        password = input("Contraseña (vacía si usas clave)", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        root.addView(host); root.addView(port); root.addView(user); root.addView(password);
        root.addView(label("Clave privada opcional", 18));
        keyButton = new Button(this); keyButton.setText("Elegir clave SSH"); keyButton.setOnClickListener(v -> pickKey()); root.addView(keyButton);
        keyName = label("Ninguna clave seleccionada", 13); root.addView(keyName);
        passphrase = input("Passphrase de la clave (si tiene)", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD); root.addView(passphrase);
        withBak = new CheckBox(this); withBak.setText("Descargar también ER0000.sl2.bak si existe"); withBak.setChecked(true); root.addView(withBak);
        backupButton = new Button(this); backupButton.setText("Conectar y descargar partida"); backupButton.setOnClickListener(v -> startBackup());
        LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(-1, dp(54)); bp.setMargins(0, dp(16), 0, dp(8)); root.addView(backupButton, bp);
        progress = new ProgressBar(this); progress.setVisibility(View.GONE); root.addView(progress, new LinearLayout.LayoutParams(-1, dp(32)));
        status = label("Listo.", 14); root.addView(status);
        root.addView(label("Busca automáticamente:\n~/.local/share/Steam/steamapps/compatdata/1245620/pfx/drive_c/users/steamuser/AppData/Roaming/EldenRing/<SteamID>/ER0000.sl2\n\nGuarda copias en Descargas/EldenRingBackups.", 13));
        setContentView(sv);
    }

    private void pickKey() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT); i.setType("*/*"); i.addCategory(Intent.CATEGORY_OPENABLE); startActivityForResult(i, PICK_KEY);
    }
    @Override protected void onActivityResult(int req, int result, Intent data) {
        super.onActivityResult(req, result, data);
        if (req == PICK_KEY && result == RESULT_OK && data != null && data.getData() != null) {
            keyUri = data.getData();
            try { getContentResolver().takePersistableUriPermission(keyUri, Intent.FLAG_GRANT_READ_URI_PERMISSION); } catch (Exception ignored) {}
            keyName.setText(keyUri.getLastPathSegment() == null ? "Clave seleccionada" : keyUri.getLastPathSegment());
        }
    }

    private void startBackup() {
        String h = host.getText().toString().trim(), ps = port.getText().toString().trim(), u = user.getText().toString().trim();
        String pw = password.getText().toString(), phrase = passphrase.getText().toString();
        if (h.isEmpty() || ps.isEmpty() || u.isEmpty()) { toast("Completa host, puerto y usuario."); return; }
        if (pw.isEmpty() && keyUri == null) { toast("Escribe la contraseña o elige una clave privada."); return; }
        int p;
        try { p = Integer.parseInt(ps); if (p < 1 || p > 65535) throw new Exception(); } catch (Exception e) { toast("Puerto SSH no válido."); return; }
        prefs.edit().putString("host", h).putString("port", ps).putString("user", u).apply();
        Uri selectedKey = keyUri; boolean wantBak = withBak.isChecked();
        busy(true, "Conectando por SSH…");
        worker.submit(() -> backup(h, p, u, pw, phrase, selectedKey, wantBak));
    }

    private void backup(String h, int p, String u, String pw, String phrase, Uri selectedKey, boolean wantBak) {
        Session session = null; ChannelSftp sftp = null;
        try {
            JSch jsch = new JSch();
            File kh = new File(getFilesDir(), "known_hosts"); if (!kh.exists()) kh.createNewFile(); jsch.setKnownHosts(kh.getAbsolutePath());
            if (selectedKey != null) {
                byte[] key = readAll(getContentResolver().openInputStream(selectedKey));
                jsch.addIdentity("android-key", key, null, phrase.isEmpty() ? null : phrase.getBytes("UTF-8"));
            }
            session = jsch.getSession(u, h, p); if (!pw.isEmpty()) session.setPassword(pw);
            session.setUserInfo(new UiUserInfo(pw, phrase));
            Properties cfg = new Properties(); cfg.put("StrictHostKeyChecking", "ask"); cfg.put("PreferredAuthentications", selectedKey != null ? "publickey,password,keyboard-interactive" : "password,keyboard-interactive"); session.setConfig(cfg);
            session.connect(12000);
            runOnUiThread(() -> status.setText("SSH conectado. Buscando la partida…"));
            sftp = (ChannelSftp) session.openChannel("sftp"); sftp.connect(10000);
            String root = sftp.pwd() + "/.local/share/Steam/steamapps/compatdata/1245620/pfx/drive_c/users/steamuser/AppData/Roaming/EldenRing";
            List<Save> saves = findSaves(sftp, root); if (saves.isEmpty()) throw new Exception("No encontré ER0000.sl2 en " + root);
            Save chosen = chooseSave(saves); if (chosen == null) { runOnUiThread(() -> busy(false, "Cancelado.")); return; }
            runOnUiThread(() -> status.setText("Descargando " + chosen.steamId + "/ER0000.sl2…"));
            String stamp = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(new Date());
            String base = "EldenRing-" + chosen.steamId + "-" + stamp;
            copyRemote(sftp, chosen.path, base + "-ER0000.sl2");
            boolean bak = false;
            if (wantBak && exists(sftp, chosen.path + ".bak")) { copyRemote(sftp, chosen.path + ".bak", base + "-ER0000.sl2.bak"); bak = true; }
            boolean gotBak = bak;
            runOnUiThread(() -> { busy(false, "Backup completado en Descargas/EldenRingBackups."); new AlertDialog.Builder(this).setTitle("Backup completado").setMessage("Partida " + chosen.steamId + " descargada correctamente." + (gotBak ? "\nTambién se guardó el .bak." : "")).setPositiveButton("OK", null).show(); });
        } catch (Exception e) {
            String m = e.getMessage() == null ? e.toString() : e.getMessage(); runOnUiThread(() -> busy(false, "Error: " + m));
        } finally {
            if (sftp != null && sftp.isConnected()) sftp.disconnect(); if (session != null && session.isConnected()) session.disconnect();
        }
    }

    private List<Save> findSaves(ChannelSftp sftp, String root) throws SftpException {
        List<Save> out = new ArrayList<>(); Vector<ChannelSftp.LsEntry> entries = sftp.ls(root);
        for (ChannelSftp.LsEntry e : entries) {
            String n = e.getFilename(); if (!e.getAttrs().isDir() || !n.matches("\\d+")) continue;
            String path = root + "/" + n + "/ER0000.sl2";
            try { SftpATTRS a = sftp.stat(path); out.add(new Save(n, path, a.getSize())); } catch (SftpException ignored) {}
        }
        return out;
    }

    private Save chooseSave(List<Save> saves) throws InterruptedException {
        if (saves.size() == 1) return saves.get(0);
        CountDownLatch latch = new CountDownLatch(1); Save[] selected = new Save[1];
        runOnUiThread(() -> {
            String[] names = new String[saves.size()]; for (int i = 0; i < saves.size(); i++) names[i] = saves.get(i).steamId + " · " + size(saves.get(i).bytes);
            new AlertDialog.Builder(this).setTitle("Elige el perfil de Elden Ring").setItems(names, (d, which) -> { selected[0] = saves.get(which); latch.countDown(); }).setOnCancelListener(d -> latch.countDown()).show();
        });
        latch.await(); return selected[0];
    }

    private boolean exists(ChannelSftp sftp, String path) { try { sftp.stat(path); return true; } catch (Exception e) { return false; } }
    private void copyRemote(ChannelSftp sftp, String remote, String name) throws Exception {
        try (InputStream in = sftp.get(remote); OutputStream out = createDownload(name)) { byte[] b = new byte[65536]; int n; while ((n = in.read(b)) > 0) out.write(b, 0, n); }
    }
    private OutputStream createDownload(String name) throws Exception {
        ContentValues v = new ContentValues(); v.put(MediaStore.Downloads.DISPLAY_NAME, name); v.put(MediaStore.Downloads.MIME_TYPE, "application/octet-stream"); v.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/EldenRingBackups");
        Uri uri = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v); if (uri == null) throw new Exception("No se pudo crear el archivo en Descargas.");
        OutputStream out = getContentResolver().openOutputStream(uri, "w"); if (out == null) throw new Exception("No se pudo abrir el archivo de destino."); return out;
    }
    private byte[] readAll(InputStream in) throws Exception {
        if (in == null) throw new Exception("No se pudo leer la clave privada.");
        try (InputStream x = in; ByteArrayOutputStream out = new ByteArrayOutputStream()) { byte[] b = new byte[8192]; int n; while ((n = x.read(b)) > 0) out.write(b, 0, n); return out.toByteArray(); }
    }
    private void busy(boolean on, String msg) { backupButton.setEnabled(!on); keyButton.setEnabled(!on); progress.setVisibility(on ? View.VISIBLE : View.GONE); status.setText(msg); }
    private void toast(String s) { Toast.makeText(this, s, Toast.LENGTH_LONG).show(); }
    private static String size(long b) { return b < 1048576 ? String.format(Locale.US, "%.1f KiB", b / 1024.0) : String.format(Locale.US, "%.1f MiB", b / 1048576.0); }
    @Override protected void onDestroy() { worker.shutdownNow(); super.onDestroy(); }

    private static class Save { final String steamId, path; final long bytes; Save(String id, String p, long b) { steamId = id; path = p; bytes = b; } }

    private final class UiUserInfo implements UserInfo {
        final String pw, phrase; UiUserInfo(String p, String ph) { pw = p; phrase = ph; }
        @Override public String getPassword() { return pw; }
        @Override public String getPassphrase() { return phrase; }
        @Override public boolean promptPassword(String m) { return !pw.isEmpty(); }
        @Override public boolean promptPassphrase(String m) { return !phrase.isEmpty(); }
        @Override public void showMessage(String m) { runOnUiThread(() -> new AlertDialog.Builder(MainActivity.this).setMessage(m).setPositiveButton("OK", null).show()); }
        @Override public boolean promptYesNo(String m) {
            CountDownLatch latch = new CountDownLatch(1); AtomicBoolean yes = new AtomicBoolean(false);
            runOnUiThread(() -> new AlertDialog.Builder(MainActivity.this).setTitle("Verificar servidor SSH").setMessage(m).setPositiveButton("Confiar", (d, w) -> { yes.set(true); latch.countDown(); }).setNegativeButton("Cancelar", (d, w) -> latch.countDown()).setOnCancelListener(d -> latch.countDown()).show());
            try { latch.await(); } catch (InterruptedException e) { Thread.currentThread().interrupt(); } return yes.get();
        }
    }
}
