package com.loonietoonie.app;

import android.app.Activity;
import android.net.Uri;
import android.util.Base64;

import androidx.activity.result.ActivityResult;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.IntentSenderRequest;
import androidx.activity.result.contract.ActivityResultContracts;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.google.mlkit.vision.documentscanner.GmsDocumentScanner;
import com.google.mlkit.vision.documentscanner.GmsDocumentScannerOptions;
import com.google.mlkit.vision.documentscanner.GmsDocumentScanning;
import com.google.mlkit.vision.documentscanner.GmsDocumentScanningResult;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.List;

/**
 * Takes a receipt photo with Google's ML Kit Document Scanner: it finds the receipt's edges,
 * crops and straightens it, and cleans it up, so OCR gets a flat image without background.
 *
 * The scanner screen runs in Google Play services (no camera permission needed) and the
 * picture stays on the phone. Used by frontend/src/capture/documentScanner.ts.
 */
@CapacitorPlugin(name = "DocumentScanner")
public class DocumentScannerPlugin extends Plugin {
    private ActivityResultLauncher<IntentSenderRequest> launcher;
    private PluginCall pending;

    @Override
    public void load() {
        // Must be registered while the activity is being created (load() runs during onCreate).
        launcher = getActivity().registerForActivityResult(
            new ActivityResultContracts.StartIntentSenderForResult(), this::onScanResult);
    }

    @PluginMethod
    public void scan(PluginCall call) {
        if (pending != null) {
            call.reject("A scan is already in progress", "BUSY");
            return;
        }
        GmsDocumentScannerOptions options = new GmsDocumentScannerOptions.Builder()
            .setScannerMode(GmsDocumentScannerOptions.SCANNER_MODE_FULL)
            .setGalleryImportAllowed(false) // the app has its own "Choose from gallery"
            .setPageLimit(1)
            .setResultFormats(GmsDocumentScannerOptions.RESULT_FORMAT_JPEG)
            .build();
        GmsDocumentScanner scanner = GmsDocumentScanning.getClient(options);
        scanner.getStartScanIntent(getActivity())
            .addOnSuccessListener(intentSender -> {
                pending = call;
                launcher.launch(new IntentSenderRequest.Builder(intentSender).build());
            })
            // e.g. Google Play services missing or too old: the web code falls back to the camera.
            .addOnFailureListener(error -> call.reject(
                "The document scanner isn't available: " + error.getMessage(), "UNAVAILABLE"));
    }

    private void onScanResult(ActivityResult activityResult) {
        PluginCall call = pending;
        pending = null;
        if (call == null) return;

        if (activityResult.getResultCode() != Activity.RESULT_OK) {
            JSObject cancelled = new JSObject();
            cancelled.put("cancelled", true);
            call.resolve(cancelled);
            return;
        }
        GmsDocumentScanningResult result =
            GmsDocumentScanningResult.fromActivityResultIntent(activityResult.getData());
        List<GmsDocumentScanningResult.Page> pages = result != null ? result.getPages() : null;
        if (pages == null || pages.isEmpty()) {
            call.reject("The scanner returned no page", "NO_PAGE");
            return;
        }
        try {
            JSObject scanned = new JSObject();
            scanned.put("cancelled", false);
            scanned.put("image", Base64.encodeToString(read(pages.get(0).getImageUri()), Base64.NO_WRAP));
            call.resolve(scanned);
        } catch (IOException error) {
            call.reject("Couldn't read the scanned page: " + error.getMessage(), "READ_FAILED");
        }
    }

    private byte[] read(Uri uri) throws IOException {
        try (InputStream input = getContext().getContentResolver().openInputStream(uri);
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            if (input == null) throw new IOException("no data at " + uri);
            byte[] buffer = new byte[64 * 1024];
            int count;
            while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            return output.toByteArray();
        }
    }
}
