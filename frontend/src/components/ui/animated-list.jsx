// Magic UI - Animated List (ported to JSX).
//
// Adapted: the upstream component reveals children on a fixed timer, which is
// meant for looping landing-page demos. Here items must animate in when they
// actually arrive from the extraction stream, so `StreamList` renders whatever
// it is given and lets AnimatePresence animate real arrivals.
import React from "react";
import { AnimatePresence, motion } from "motion/react";

import { cn } from "@/lib/utils";

export function AnimatedListItem({ children }) {
  return (
    <motion.div
      initial={{ scale: 0.94, opacity: 0, y: -8, filter: "blur(4px)" }}
      animate={{ scale: 1, opacity: 1, y: 0, filter: "blur(0px)", originY: 0 }}
      exit={{ scale: 0.96, opacity: 0 }}
      transition={{ type: "spring", stiffness: 350, damping: 40 }}
      layout
      className="w-full"
    >
      {children}
    </motion.div>
  );
}

export function StreamList({ children, className, ...props }) {
  const items = React.Children.toArray(children);
  return (
    <div className={cn("flex flex-col gap-2", className)} {...props}>
      <AnimatePresence initial={false}>
        {items.map((item) => (
          <AnimatedListItem key={item.key}>{item}</AnimatedListItem>
        ))}
      </AnimatePresence>
    </div>
  );
}
