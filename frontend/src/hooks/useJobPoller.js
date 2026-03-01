import { useState, useEffect, useRef, useCallback } from "react";
import { getJob } from "../api/client";

/**
 * Poll a background job until it completes or errors.
 * Returns { job, startPolling, reset }.
 */
export function useJobPoller() {
  const [job, setJob] = useState(null);
  const intervalRef = useRef(null);
  const timeoutRef = useRef(null);

  const stopPolling = useCallback(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (jobId) => {
      stopPolling();
      setJob({ job_id: jobId, status: "pending", progress: 0, output_paths: [] });

      const poll = async () => {
        try {
          const data = await getJob(jobId);
          setJob(data);
          if (data.status === "done" || data.status === "error" || data.status === "cancelled" || data.status === "retrying") {
            stopPolling();
          }
        } catch {
          // Don't stop on first error (e.g. 404 when load-balanced); keep last state and retry
        }
      };

      // First poll soon so progress (e.g. "Обработка изображений: N/M") appears quickly
      timeoutRef.current = setTimeout(poll, 150);
      intervalRef.current = setInterval(poll, 800);
    },
    [stopPolling]
  );

  const reset = useCallback(() => {
    stopPolling();
    setJob(null);
  }, [stopPolling]);

  useEffect(() => stopPolling, [stopPolling]);

  return { job, startPolling, reset };
}
