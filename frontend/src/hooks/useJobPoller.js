import { useState, useEffect, useRef, useCallback } from "react";
import { getJob } from "../api/client";

/**
 * Poll a background job until it completes or errors.
 * Returns { job, startPolling, reset }.
 */
export function useJobPoller() {
  const [job, setJob] = useState(null);
  const intervalRef = useRef(null);

  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (jobId) => {
      stopPolling();
      setJob({ job_id: jobId, status: "pending", progress: 0, output_paths: [] });

      intervalRef.current = setInterval(async () => {
        try {
          const data = await getJob(jobId);
          setJob(data);
          if (data.status === "done" || data.status === "error" || data.status === "cancelled" || data.status === "retrying") {
            stopPolling();
          }
        } catch {
          stopPolling();
        }
      }, 1500);
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
