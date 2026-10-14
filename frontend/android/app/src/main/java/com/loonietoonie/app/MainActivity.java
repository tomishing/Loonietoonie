package com.loonietoonie.app;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        // Local plugins must be registered before the bridge starts.
        registerPlugin(ReceiptOcrPlugin.class);
        registerPlugin(DocumentScannerPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
