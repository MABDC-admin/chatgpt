"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearToken, formatCents, type Conversation, type Credits, type Me } from "@/lib/api";
import { useTheme } from "@/components/ThemeProvider";
import { cn } from "@/lib/utils";
import {
  MessageSquare,
  Wrench,
  Image,
  BookOpen,
  Shield,
  User,
  Plus,
  Search,
  MoreHorizontal,
  Pencil,
  Archive,
  Trash2,
  Sun,
  Moon,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  X,
  FolderOpen,
} from "lucide-react";

type Props = {
  me: Me | null;
  credits: Credits | null;
  conversations?: Conversation[];
  activeId?: string | null;
  onSelect?: (id: string | null) => void;
  onSearch?: (term: string) => void;
  onRename?: (id: string, title: string) => void;
  onArchive?: (id: string) => void;
  onDelete?: (id: string) => void;
};

const NAV_ITEMS = [
  { href: "/chat", label: "Chat", icon: MessageSquare, permission: null },
  { href: "/tools", label: "Teacher Tools", icon: Wrench, permission: null },
  { href: "/images", label: "Images", icon: Image, permission: null },
  { href: "/files", label: "Files", icon: FolderOpen, permission: null },
  { href: "/knowledge", label: "Knowledge Base", icon: BookOpen, permission: null },
  { href: "/admin", label: "Admin", icon: Shield, permission: "users.read" },
  { href: "/account", label: "Account", icon: User, permission: null },
];

export default function Sidebar({
  me,
  credits,
  conversations,
  activeId,
  onSelect,
  onSearch,
  onRename,
  onArchive,
  onDelete,
}: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const { theme, toggle } = useTheme();
  const [open, setOpen] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [menuFor, setMenuFor] = useState<string | null>(null);

  useEffect(() => {
    if (!onSearch) return;
    const timer = setTimeout(() => onSearch(term), 250);
    return () => clearTimeout(timer);
  }, [term, onSearch]);

  useEffect(() => {
    if (!menuFor) return;
    const close = () => setMenuFor(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [menuFor]);

  function signOut() {
    clearToken();
    router.push("/login");
  }

  function startRename(convo: Conversation) {
    const next = window.prompt("Rename this conversation:", convo.title);
    if (next && next.trim() && next.trim() !== convo.title) {
      onRename?.(convo.id, next.trim());
    }
  }

  const initials = me?.full_name
    ? me.full_name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase()
    : "?";

  const sidebarContent = (
    <div className="flex h-full flex-col bg-white">
      {/* Header */}
      <div className={cn("flex items-center py-3 border-b border-[#d1fae5] shrink-0", open ? "justify-between px-3" : "justify-center px-1")}>
        <div className={cn("flex items-center", open ? "gap-2.5" : "gap-0")}>
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-50 border border-[#d1fae5] text-emerald-600 shrink-0">
            <MessageSquare className="h-4 w-4" />
          </div>
          {open && <h1 className="text-sm font-bold text-[#1f2937] whitespace-nowrap">MABDC CHATGPT</h1>}
        </div>
        {open && (
          <div className="flex items-center gap-1">
            {onSelect && (
              <button
                onClick={() => onSelect(null)}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-emerald-600 px-3 text-sm font-medium text-white hover:bg-emerald-700 transition-colors shadow-sm"
                title="New chat"
              >
                <Plus className="h-4 w-4" />
                <span className="hidden sm:inline">New chat</span>
              </button>
            )}
            <button
              onClick={() => setOpen(!open)}
              className="hidden md:inline-flex h-8 w-8 items-center justify-center rounded-md text-[#6b7280] hover:bg-emerald-50 hover:text-emerald-700 transition-colors"
              title="Toggle sidebar"
            >
              <PanelLeftClose className="h-4 w-4" />
            </button>
            <button
              onClick={() => setMobileOpen(false)}
              className="md:hidden inline-flex h-8 w-8 items-center justify-center rounded-md text-[#6b7280] hover:bg-emerald-50 hover:text-emerald-700 transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        )}
        {!open && (
          <button
            onClick={() => setOpen(true)}
            className="hidden md:inline-flex h-8 w-8 items-center justify-center rounded-md text-[#6b7280] hover:bg-emerald-50 hover:text-emerald-700 transition-colors"
            title="Expand sidebar"
          >
            <PanelLeftOpen className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Search */}
      {onSearch && (
        <div className="px-3 pb-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[#6b7280]" />
            <input
              type="text"
              placeholder="Search conversations…"
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              className="w-full rounded-lg border border-[#d1fae5] bg-[#f8fafc] py-1.5 pl-8 pr-3 text-sm text-[#1f2937] placeholder:text-[#6b7280] focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-emerald-400"
            />
          </div>
        </div>
      )}

      {/* Conversations list — always render the flex-1 container so nav+footer stay pinned to bottom */}
      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5">
        {conversations?.map((c) => (
            <div
              key={c.id}
              className={cn(
                "group flex items-center rounded-md cursor-pointer transition-colors relative",
                c.id === activeId
                  ? "bg-emerald-50 text-[#1f2937] font-medium border-l-[3px] border-emerald-600 pl-[calc(0.75rem-3px)]"
                  : "hover:bg-emerald-50 text-[#1f2937] border-l-[3px] border-transparent pl-[calc(0.75rem-3px)]"
              )}
              onClick={() => onSelect?.(c.id)}
            >
              <span className="flex-1 truncate px-3 py-2 text-sm">
                {c.title}
              </span>
              <div className="relative mr-1 hidden group-hover:block">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setMenuFor(menuFor === c.id ? null : c.id);
                  }}
                  className="inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-400 hover:bg-emerald-50 hover:text-emerald-700 transition-colors"
                >
                  <MoreHorizontal className="h-3.5 w-3.5" />
                </button>
                {menuFor === c.id && (
                  <div className="absolute right-0 top-8 z-20 min-w-[140px] rounded-lg border border-emerald-100 bg-white p-1 shadow-lg">
                    <button
                      onClick={(e) => { e.stopPropagation(); setMenuFor(null); startRename(c); }}
                      className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-gray-700 hover:bg-emerald-50"
                    >
                      <Pencil className="h-3.5 w-3.5" /> Rename
                    </button>
                    {onArchive && (
                      <button
                        onClick={(e) => { e.stopPropagation(); setMenuFor(null); onArchive(c.id); }}
                        className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-gray-700 hover:bg-emerald-50"
                      >
                        <Archive className="h-3.5 w-3.5" /> Archive
                      </button>
                    )}
                    {onDelete && (
                      <button
                        onClick={(e) => { e.stopPropagation(); setMenuFor(null); onDelete(c.id); }}
                        className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-red-600 hover:bg-red-50"
                      >
                        <Trash2 className="h-3.5 w-3.5" /> Delete
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          ))}
      </div>

      {/* Navigation links */}
      <nav className={cn("border-t border-emerald-100 py-2 space-y-0.5 shrink-0", open ? "px-2" : "px-1")}>
        {NAV_ITEMS.filter((item) => !item.permission || me?.permissions?.includes(item.permission)).map(({ href, label, icon: Icon }) => {
          const isActive = pathname === href || pathname?.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              title={!open ? label : undefined}
              className={cn(
                "flex items-center rounded-md text-sm transition-all duration-200",
                open ? "gap-3 px-3 py-2" : "justify-center px-2 py-2.5",
                isActive
                  ? cn("bg-emerald-50 text-emerald-700 font-medium", open && "border-l-[3px] border-emerald-600 pl-[calc(0.75rem-3px)]")
                  : cn("text-[#6b7280] hover:bg-emerald-50 hover:text-[#1f2937]", open && "border-l-[3px] border-transparent pl-[calc(0.75rem-3px)]")
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {open && <span className="truncate">{label}</span>}
            </Link>
          );
        })}
      </nav>

      {/* Footer: user info + theme toggle + sign out — pinned to bottom */}
      <div className="mt-auto border-t border-[#d1fae5] px-3 py-3 space-y-2 shrink-0">
        {me && (
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-emerald-50 border border-[#d1fae5] text-xs font-semibold text-emerald-700">
              {initials}
            </div>
            <div className="flex-1 min-w-0">
              <p className="truncate text-sm font-medium text-[#1f2937]">{me.full_name}</p>
              <p className="text-xs text-[#6b7280]">
                {me.role.replace(/_/g, " ").toLowerCase()}
                {credits && credits.monthly_allocation_cents > 0 && (
                  <span className="text-emerald-600 font-medium"> · Credits {credits.remaining_cents}</span>
                )}
              </p>
            </div>
          </div>
        )}
        <div className="flex items-center gap-1">
          <button
            onClick={toggle}
            className="inline-flex h-8 flex-1 items-center justify-center gap-2 rounded-md text-sm text-[#6b7280] hover:bg-emerald-50 hover:text-[#1f2937] transition-colors"
          >
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </button>
          <button
            onClick={signOut}
            className="inline-flex h-8 flex-1 items-center justify-center gap-2 rounded-md text-sm text-[#6b7280] hover:bg-red-50 hover:text-red-600 transition-colors"
          >
            <LogOut className="h-4 w-4" />
            Sign out
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <>
      {/* Mobile toggle button */}
      <button
        onClick={() => setMobileOpen(true)}
        className="fixed left-3 top-3 z-30 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[#d1fae5] bg-white shadow-sm text-emerald-600 hover:bg-emerald-50 transition-colors md:hidden"
      >
        <PanelLeftOpen className="h-4 w-4" />
      </button>

      {/* Mobile scrim */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar panel */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex flex-col border-r border-[#d1fae5] bg-white transition-all duration-200 md:relative md:z-auto",
          open ? "w-[260px]" : "w-0 md:w-[60px] overflow-hidden",
          mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"
        )}
      >
        {sidebarContent}
      </aside>
    </>
  );
}
