import { defineStore } from "pinia";

import {
  fetchDates,
  fetchDayInventory,
  fetchSnapshot,
  type DayInventory,
  type Snapshot,
} from "../api/simDaily";

export const useSimTrackerStore = defineStore("simTracker", {
  state: () => ({
    dates: [] as string[],
    selectedDate: null as string | null,
    selectedTimepoint: null as string | null,
    inventory: null as DayInventory | null,
    snapshot: null as Snapshot | null,
    loading: false,
    error: null as string | null,
  }),
  getters: {
    availableTimepoints(): string[] {
      return (this.inventory?.timepoints ?? []).map((tp) => tp.key);
    },
  },
  actions: {
    async init() {
      const { dates, latest } = await fetchDates();
      this.dates = dates;
      if (latest && !this.selectedDate) {
        await this.selectDate(latest);
      }
    },
    async selectDate(date: string) {
      this.selectedDate = date;
      this.snapshot = null;
      this.inventory = await fetchDayInventory(date);
      const tps = this.inventory.timepoints;
      const target = tps.find((tp) => tp.key === "v1") ?? tps[tps.length - 1];
      if (target) await this.selectTimepoint(target.key);
    },
    async selectTimepoint(timepoint: string) {
      if (!this.selectedDate) return;
      this.selectedTimepoint = timepoint;
      this.snapshot = await fetchSnapshot(this.selectedDate, timepoint);
    },
    async refreshCurrent() {
      if (!this.selectedDate) return;
      this.inventory = await fetchDayInventory(this.selectedDate);
      if (this.selectedTimepoint && this.availableTimepoints.includes(this.selectedTimepoint)) {
        this.snapshot = await fetchSnapshot(this.selectedDate, this.selectedTimepoint);
      }
    },
  },
});
