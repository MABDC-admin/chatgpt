"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import { api, formatCents, getToken, type Credits, type Me } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { AlertCircle, User, Mail, Shield, CreditCard } from "lucide-react";

export default function AccountPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [credits, setCredits] = useState<Credits | null>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [user, credit] = await Promise.all([api<Me>("/auth/me"), api<Credits>("/auth/me/credits")]);
    setMe(user);
    setCredits(credit);
  }, []);

  useEffect(() => {
    if (!getToken()) { router.replace("/login"); return; }
    load().catch((e) => setError(e instanceof Error ? e.message : "Could not load account"));
  }, [router, load]);

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    if (next !== confirm) { setError("The new passwords do not match."); return; }
    if (next.length < 10) { setError("The new password must be at least 10 characters."); return; }
    setSaving(true);
    try {
      await api("/auth/me/password", { method: "POST", body: JSON.stringify({ current_password: current, new_password: next }) });
      setNotice("Password changed successfully.");
      setCurrent(""); setNext(""); setConfirm("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change your password");
    } finally { setSaving(false); }
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[#f8fafc]">
      <Sidebar me={me} credits={credits} />
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-6 py-8 space-y-6">
          <div>
            <h1 className="text-xl font-semibold text-[#1f2937]">Your Account</h1>
            <p className="text-sm text-[#6b7280] mt-1">Profile information and password settings</p>
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" /><span>{error}</span>
            </div>
          )}
          {notice && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-400">{notice}</div>
          )}

          {/* Profile info */}
          {me && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Card className="border-emerald-100 bg-white"><CardContent className="p-4 flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-100"><User className="h-5 w-5 text-emerald-600" /></div>
                <div><p className="text-xs text-[#6b7280]">Name</p><p className="text-sm font-semibold text-[#1f2937]">{me.full_name}</p></div>
              </CardContent></Card>
              <Card className="border-emerald-100 bg-white"><CardContent className="p-4 flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-100"><Mail className="h-5 w-5 text-emerald-600" /></div>
                <div><p className="text-xs text-[#6b7280]">Email</p><p className="text-sm font-semibold text-[#1f2937]">{me.email}</p></div>
              </CardContent></Card>
              <Card className="border-emerald-100 bg-white"><CardContent className="p-4 flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-100"><Shield className="h-5 w-5 text-emerald-600" /></div>
                <div><p className="text-xs text-[#6b7280]">Role</p><Badge variant="secondary" className="mt-0.5 bg-emerald-50 text-emerald-700 border-emerald-200">{me.role.replace(/_/g, " ")}</Badge></div>
              </CardContent></Card>
              {credits && (
                <Card className="border-emerald-100 bg-white"><CardContent className="p-4 flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-100"><CreditCard className="h-5 w-5 text-emerald-600" /></div>
                  <div>
                    <p className="text-xs text-[#6b7280]">Credits this month</p>
                    <p className="text-sm font-semibold text-[#1f2937]">
                      {credits.monthly_allocation_cents === 0 ? `${formatCents(credits.used_cents)} used (unmetered)` : `${formatCents(credits.remaining_cents)} remaining`}
                    </p>
                  </div>
                </CardContent></Card>
              )}
            </div>
          )}

          {/* Change password */}
          <Card className="max-w-md border-emerald-100 bg-white">
            <CardHeader><CardTitle className="text-base text-[#1f2937]">Change Password</CardTitle></CardHeader>
            <CardContent>
              <form onSubmit={changePassword} className="space-y-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#6b7280]">Current password</label>
                  <Input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#6b7280]">New password</label>
                  <Input type="password" autoComplete="new-password" required minLength={10} placeholder="At least 10 characters" value={next} onChange={(e) => setNext(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium text-[#6b7280]">Confirm new password</label>
                  <Input type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
                </div>
                <Button type="submit" disabled={saving || !current || !next || !confirm} className="w-full bg-emerald-600 hover:bg-emerald-700 text-white">
                  {saving ? "Saving…" : "Change password"}
                </Button>
              </form>
            </CardContent>
          </Card>
        </div>
      </main>
    </div>
  );
}
