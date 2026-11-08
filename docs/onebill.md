# Online fee payment with 1Bill

Students and parents pay a challan from any bank app, JazzCash, Easypaisa, an ATM or a bank counter by choosing
**1Bill → Invoice/Voucher → VIST** and entering the challan's **consumer number**. The money goes to the institute's
bank account; the system marks the challan paid, issues the receipt, pays the late fee (as a fine) if it was late,
tells the student (app + SMS), and lists the payment under **Accounts → 1Bill payments** for the daily match.

## What the bank network calls

| | Address | Sends | We answer |
|---|---|---|---|
| Bill inquiry | `POST https://<your-site>/api/v1/onebill/inquiry` | `consumer_number`, `bank_mnemonic` | status, due date, amount before and after the due date |
| Bill payment | `POST https://<your-site>/api/v1/onebill/payment` | `consumer_number`, `tran_auth_id`, `transaction_amount`, `tran_date`, `tran_time`, `bank_mnemonic` | `00` accepted (with the receipt number) or a refusal code |

Login: the `username` / `password` headers (or HTTP Basic) that you agree with the bank. Answer codes: `00` OK,
`01` consumer number not found, `02` challan cancelled / fee not active, `03` already paid, `04` wrong amount or data,
`05` system problem (nothing was recorded). Amounts: `+0000000150000` = Rs. 1,500.00. Dates: `YYYYMMDD`.

**The bank will send its own "1Bill biller integration document".** Field names or codes in it may differ from the
above; everything about the format is in one place (`app/fees/onebill.py`, the FORMAT section) and is adjusted there.

## The amount asked for

challan (what is still due on that instalment) + late fee built up to the day of payment (only when Settings → Fees →
"add the late fee to Fines when a late instalment is paid" is on) + the online payment charge (Settings → Fees →
Online payment: a fixed Rs. amount and/or a % of the challan). A payment for any other amount is refused, so the payer's
bank does not take the money. The same payment sent twice is accepted once.

## Going live (on the server, from /opt/vist-lms)

1. Put the bank's details in `.env`: `ONE_BILL_USERNAME=…`, `ONE_BILL_PASSWORD=…`, optionally `ONE_BILL_ALLOWED_IPS=…`,
   then `ONE_BILL_ENABLED=1`.
2. Restart: `docker compose -f docker-compose.prod.yml up -d app`.
3. Do one test payment with the bank (their test consumer number or a real Rs. challan), then check
   Accounts → 1Bill payments shows it and the student's fee shows it paid.
4. Send the 1Bill document to your developer if anything is refused; the format is adjusted in one file.
