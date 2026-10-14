package com.loonietoonie.app;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Rect;
import android.util.Base64;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.google.android.gms.tasks.Task;
import com.google.android.gms.tasks.Tasks;
import com.google.mlkit.vision.common.InputImage;
import com.google.mlkit.vision.text.Text;
import com.google.mlkit.vision.text.TextRecognition;
import com.google.mlkit.vision.text.TextRecognizer;
import com.google.mlkit.vision.text.japanese.JapaneseTextRecognizerOptions;
import com.google.mlkit.vision.text.latin.TextRecognizerOptions;

/**
 * On-device receipt OCR with Google ML Kit (photos never leave the phone).
 *
 * Runs the Latin and Japanese recognizers on the same image and returns the lines of both,
 * with their bounding boxes. The web code (frontend/src/ocr/native.ts) picks the script and
 * rebuilds receipt rows ("item ... price") from the boxes.
 */
@CapacitorPlugin(name = "ReceiptOcr")
public class ReceiptOcrPlugin extends Plugin {
    private TextRecognizer latin;
    private TextRecognizer japanese;

    @Override
    public void load() {
        latin = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS);
        japanese = TextRecognition.getClient(new JapaneseTextRecognizerOptions.Builder().build());
    }

    @PluginMethod
    public void recognize(PluginCall call) {
        String data = call.getString("image");
        if (data == null || data.isEmpty()) {
            call.reject("image (base64 JPEG or PNG) is required");
            return;
        }
        Bitmap bitmap;
        try {
            byte[] bytes = Base64.decode(data, Base64.DEFAULT);
            bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
        } catch (IllegalArgumentException e) {
            call.reject("image is not valid base64");
            return;
        }
        if (bitmap == null) {
            call.reject("image could not be decoded");
            return;
        }

        // The web code already applied the photo's EXIF rotation, so rotation is 0 here.
        InputImage image = InputImage.fromBitmap(bitmap, 0);
        Task<Text> latinTask = latin.process(image);
        Task<Text> japaneseTask = japanese.process(image);
        Tasks.whenAllComplete(latinTask, japaneseTask).addOnCompleteListener(done -> {
            if (!latinTask.isSuccessful() && !japaneseTask.isSuccessful()) {
                Exception error = latinTask.getException();
                call.reject("Text recognition failed: " + (error != null ? error.getMessage() : "unknown"));
                return;
            }
            JSObject result = new JSObject();
            result.put("width", bitmap.getWidth());
            result.put("height", bitmap.getHeight());
            result.put("latin", latinTask.isSuccessful() ? lines(latinTask.getResult()) : new JSArray());
            result.put("japanese", japaneseTask.isSuccessful() ? lines(japaneseTask.getResult()) : new JSArray());
            call.resolve(result);
        });
    }

    private static JSArray lines(Text text) {
        JSArray lines = new JSArray();
        for (Text.TextBlock block : text.getTextBlocks()) {
            for (Text.Line line : block.getLines()) {
                Rect box = line.getBoundingBox();
                if (box == null) continue;
                JSObject item = new JSObject();
                item.put("text", line.getText());
                item.put("left", box.left);
                item.put("top", box.top);
                item.put("right", box.right);
                item.put("bottom", box.bottom);
                lines.put(item);
            }
        }
        return lines;
    }

    @Override
    protected void handleOnDestroy() {
        if (latin != null) latin.close();
        if (japanese != null) japanese.close();
    }
}
