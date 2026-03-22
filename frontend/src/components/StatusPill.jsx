import React from 'react';

export const StatusPill = ({ label, value }) => {
  return (
    <div className="status-pill">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
};
