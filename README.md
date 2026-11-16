# OpsPilot AI

## Run locally

1. Copy `.env.example` to `.env` and set `LLM_API_KEY`.
2. Set `REPORT_API_TOKEN` to a long random value. The Python API and Node
   report service both read this same token from the root `.env` file.
3. Install the PDF service dependencies:

   ```powershell
   npm --prefix report_service install
   ```

4. Start the PDF report service in one terminal:

   ```powershell
   npm --prefix report_service start
   ```

5. Start the API in another terminal:

   ```powershell
   python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 4000
   ```

Open `http://127.0.0.1:4000`. After a diagnosis completes, use **Download PDF
report** to generate and download the report.