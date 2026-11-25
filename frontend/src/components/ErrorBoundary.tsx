"use client";

import React from "react";
import { Notice, btn } from "@/components/ui";

interface Props {
  title: string;
  children: React.ReactNode;
}

export class ErrorBoundary extends React.Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error) {
    console.error(error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <Notice
        tone="err"
        title={this.props.title}
        action={
          <button type="button" className={btn.secondary} onClick={() => this.setState({ failed: false })}>
            Try again
          </button>
        }
      >
        This part couldn&apos;t be shown. The rest of the page still works.
      </Notice>
    );
  }
}
